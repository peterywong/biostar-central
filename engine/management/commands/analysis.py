import os, sys, logging, hjson, textwrap
from django.core.management.base import BaseCommand
from engine.models import Job, Project, Analysis, User, Data
from biostar.tools.const import DATA_TYPES
from biostar.tools import defaults

logger = logging.getLogger('engine')

__CURR_DIR = os.path.dirname(os.path.realpath(__file__))

class Command(BaseCommand):
    help = 'Manages analyses.'

    def add_arguments(self, parser):

        parser.add_argument('--add', action='store_true', default=False,
                            help="Adds an analysis to a project")
        parser.add_argument('--id', default=1,
                            help="Specifies the project id")
        parser.add_argument('--json',
                            help="The json specification file")
        parser.add_argument('--template',
                            help="The template for the analysis")
        parser.add_argument('--create_job', action='store_true', default=False,
                            help="Also creates a queued job for the analysis")

        # TODO: Impove the help for usage
        parser.add_argument('--analysis_usage',
                            help=f"Who this job/analysis meant for.",
                            default=defaults.USAGE, choices=dict(Analysis.USAGE_CHOICES).values())
        parser.add_argument('--project_usage',
                            help=f"Who this job/analysis meant for.",
                            default=defaults.USAGE, choices=dict(Analysis.USAGE_CHOICES).values())


    def handle(self, *args, **options):

        add = options['add']
        json = options['json']
        pid = options['id']
        template = options['template']
        create_job = options['create_job']

        usage_map = lambda dictionary: {y: x for x, y in dictionary.items()}

        analysis_usage = usage_map(dict(Analysis.USAGE_CHOICES)).get(options['analysis_usage'], Analysis.USER)
        project_usage = usage_map(dict(Project.USAGE_CHOICES)).get(options['analysis_usage'], Project.USER)

        admin = User.objects.filter(is_staff=True).first()
        if not admin:
            logger.error("site has no admin users")
            return

        if not add:
            logger.error("command requires at least one action: --add --delete")
            return

        if add:

            if not (json and template):
                logger.error("this command requires --json --template to be set")
                return

            project = Project.objects.filter(id=pid).first()
            Project.objects.filter(id=pid).update(usage=project_usage)

            if not project:
                logger.error(f'No project with id={pid}')
                return

            if not os.path.isfile(json):
                logger.error(f'No file found for --json={json}')
                return

            if not os.path.isfile(template):
                logger.error(f'No file found for --template={template}')
                return

            try:
                # Parse the json_text into json_data
                json_text = open(json).read()
                json_data = hjson.loads(json_text)
            except Exception as exc:
                logger.error(f"error leading the template: {exc}")
                return

            try:
                # Read the specification
                template = open(template).read()
            except Exception as exc:
                logger.error(f"error reading out the spec: {exc}")
                return

            try:
                name = json_data.get("settings", {}).get("name", "No name set")
                text = json_data.get("settings", {}).get("help", "No help set")
                text = textwrap.dedent(text)
                summary = json_data.get("settings", {}).get("summary", "No summary set")
                analysis = project.create_analysis(json_text=json_text, summary=summary,
                                                   template=template, name=name, text=text, usage=analysis_usage)
                logger.info(f"Added analysis '{analysis.name}' to project id={project.id}")

                # Also create a queued job:
                if create_job:
                    # Need to deposit the file as data into the project.
                    # Find all objects that have a path attribute
                    for key, value in json_data.items():
                        path = value.get("path")
                        data_type = value.get("data_type")
                        data_type = DATA_TYPES.get(data_type)
                        if path:
                            data = project.create_data(fname=path, data_type=data_type)
                            data.fill_dict(value)
                    analysis.create_job(json_data=json_data, usage=analysis_usage)

            except KeyError as exc:
                logger.error(f"processing the analysis: {exc}")
                return
