# exit on error
set -o errexit

poetry install --no-dev

poetry run python manage.py collectstatic --no-input
poetry run python manage.py migrate