from functools import wraps, partial
import logging
from datetime import datetime, timedelta
from django.utils import dateparse
import json
import os
import logging
from os.path import join, normpath
from django.core.cache import cache
from django.conf import settings
from datetime import datetime, timedelta

from django.http import HttpResponse

from whoosh.searching import Results

from biostar.accounts.models import Profile, User
from . import util
from .models import Post, Vote, Subscription, PostView


logger = logging.getLogger("engine")


def api_error(msg="Api Error"):
    return {'error': msg}

def stat_file(date, data=None, load=False, dump=False):

    os.makedirs(settings.STATS_DIR, exist_ok=True)
    file_name = f'{date.year}-{date.month}-{date.day}.json'
    file_path = normpath(join(settings.STATS_DIR, file_name))

    def load_file():
        # This will be FileNotFoundError in Python3.
        if not os.path.isfile(file_path):
            raise IOError
        with open(file_path, 'r') as fin:
            return json.loads(fin.read())

    def dump_into_file():
        with open(file_path, 'w') as fout:
            fout.write(json.dumps(data))

    if load:
        return load_file()

    if dump:
        return dump_into_file()


def compute_stats(date):
    """
    Statistics about this website for the given date.
    Statistics are stored to a json file for caching purpose.

    Parameters:
    date -- a `datetime`.
    """

    start = date.date()
    end = start + timedelta(days=1)

    try:
        return stat_file(date=start, load=True)
    except Exception as exc:  # This will be FileNotFoundError in Python3.
        logger.info('No stats file for {}.'.format(start))

    query = Post.objects.filter

    questions = query(type=Post.QUESTION, creation_date__lt=end).count()
    answers = query(type=Post.ANSWER, creation_date__lt=end).count()
    toplevel = query(type__in=Post.TOP_LEVEL, creation_date__lt=end).exclude(type=Post.BLOG).count()
    comments = query(type=Post.COMMENT, creation_date__lt=end).count()
    votes = Vote.objects.filter(date__lt=end).count()
    users = User.objects.filter(profile__date_joined__lt=end).count()

    new_users = User.objects.filter(profile__date_joined__gte=start, profile__date_joined__lt=end)
    new_users_ids = [user.id for user in new_users]

    new_posts = Post.objects.filter(creation_date__gte=start, creation_date__lt=end)
    new_posts_ids = [post.id for post in new_posts]

    new_votes = Vote.objects.filter(date__gte=start, date__lt=end)
    new_votes_ids = [vote.id for vote in new_votes]

    data = {
        'date': util.datetime_to_iso(start),
        'timestamp': util.datetime_to_unix(start),
        'questions': questions,
        'answers': answers,
        'toplevel': toplevel,
        'comments': comments,
        'votes': votes,
        'users': users,
        'new_users': new_users_ids,
        'new_posts': new_posts_ids,
        'new_votes': new_votes_ids,
    }

    if not settings.DEBUG:
        stat_file(dump=True, date=start, data=data)
    return data


def json_response(f):
    """
    Converts any functions which returns a dictionary to a proper HttpResponse with json content.
    """
    def to_json(request, *args, **kwargs):
        """
        Creates the actual HttpResponse with json content.
        """
        try:
            data = f(request, *args, **kwargs)
        except Exception as exc:
            logger.error(exc)
            data = api_error(msg=f"Error: {exc}")

        payload = json.dumps(data, sort_keys=True, indent=4)
        response = HttpResponse(payload, content_type="application/json")
        if not data:
            response.status_code = 404
            response.reason_phrase = 'Not found'
        return response
    return to_json


@json_response
def batch_posts(request):
    """
    Return batch of posts with a set size and starting from a given date.
    """
    # Size of data to return
    batch_size = request.GET("batch_size") or 10

    # Start date in ISO8601 format, like: 2014-05-20T06:11:41.733900.
    start_date = request.GET("start_date")

    if not start_date:
        msg = "Start date ( start_date ) parameter required in GET request."
        return api_error(msg=msg)

    batch = Post.objects.filter(lastedit_date__gte=start_date)[:batch_size]
    data = {}

    for post in batch:
        data.setdefault('posts', []).append(post.json_data())

    return data


@json_response
def daily_stats_on_day(request, day):
    """
    Statistics about this website for the given day.
    Day-0 is the day of the first post.

    Parameters:
    day -- a day, given as a number of days from day-0 (the day of the first post).
    """
    store = cache.get('default')
    day_zero = cache.get('day_zero')
    first_post = Post.objects.order_by('creation_date').only('creation_date')

    if day_zero is None and not first_post:
        return False

    if day_zero is None:
        day_zero = first_post[0].creation_date
        store.set('day_zero', day_zero, 60 * 60 * 24 * 7)  # Cache valid for a week.

    date = day_zero + timedelta(days=int(day))

    # We don't provide stats for today or the future.
    if not date or date.date() >= datetime.today().date():
        return {}
    return compute_stats(date)


@json_response
def daily_stats_on_date(request, year, month, day):
    """
    Statistics about this website for the given date.

    Parameters:
    year -- Year, 4 digits.
    month -- Month, 2 digits.
    day -- Day, 2 digits.
    """
    date = datetime(int(year), int(month), int(day))
    # We don't provide stats for today or the future.
    if date.date() >= datetime.today().date():
        return {}
    return compute_stats(date)


@json_response
def traffic(request):
    """
    Traffic as post views in the last 60 min.
    """
    now = datetime.now()
    start = now - timedelta(minutes=60)

    post_views = PostView.objects.filter(date__gt=start).exclude(date__gt=now).distinct('ip').count()

    data = {
        'date': util.datetime_to_iso(now),
        'timestamp': util.datetime_to_unix(now),
        'post_views_last_60_min': post_views,
    }
    return data


@json_response
def user_email(request, email):
    try:
        user = User.objects.get(email__iexact=email.lower())
        return True
    except User.DoesNotExist:
        return False


@json_response
def user_details(request, id):
    """
    Details for a user.

    Parameters:
    id -- the id of the `User`.
    """
    try:
        user = User.objects.get(pk=id)
    except User.DoesNotExist:
        return {}

    days_ago = (datetime.now().date() - user.profile.date_joined.date()).days
    data = {
        'id': user.id,
        'uid': user.profile.uid,
        'name': user.name,
        'date_joined': util.datetime_to_iso(user.profile.date_joined),
        'last_login': util.datetime_to_iso(user.profile.last_login),
        'joined_days_ago': days_ago,
        'vote_count': Vote.objects.filter(author=user).count(),
    }
    return data


@json_response
def post_details(request, id):
    """
    Details for a post.

    Parameters:
    id -- the id of the `Post`.
    """
    try:
        post = Post.objects.get(pk=id)
    except Post.DoesNotExist:
        return {}
    return post.json_data()


@json_response
def vote_details(request, id):
    """
    Details for a vote.

    Parameters:
    id -- the id of the `Vote`.
    """
    try:
        vote = Vote.objects.get(pk=id)
    except Vote.DoesNotExist:
        return {}

    data = {
        'id': vote.id,
        'author_id': vote.author.id,
        'author': vote.author.name,
        'post_id': vote.post.id,
        'type': vote.get_type_display(),
        'type_id': vote.type,
        'date': util.datetime_to_iso(vote.date),
    }
    return data