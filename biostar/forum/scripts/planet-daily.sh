
UPDATE_COUNT=5

cd /export/www/biostar-central/

# Load the conda commands.
source ~/miniconda3/etc/profile.d/conda.sh

export POSTGRES_HOST=/var/run/postgresql
# Activate the conda environemnt.
conda activate engine

# Stop on errors.
set -ue

# Set the configuration module.
export DJANGO_SETTINGS_MODULE=conf.run.site_settings

# Update latest five entries for each planet blog.
python manage.py planet --update ${UPDATE_COUNT}
