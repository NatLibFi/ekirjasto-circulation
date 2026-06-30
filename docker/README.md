# Docker

## Using This Image

You will need **a PostgreSQL instance URL** in the format
`postgresql://[username]:[password]@[host]:[port]/[database_name]`. Check the `./docker-compose.yml` file for an example.
With this URL, you can create containers for both the web application (`circ-webapp`) and for the background cron jobs
that import and update books and otherwise keep the app running smoothly (`circ-scripts`). Either container can be used
to initialize or migrate the database. Database initialization uses a PostgreSQL Advisory Lock to make sure that only
one instance is updating the schema at a time. Database migration uses Alembic to update the schema to the latest
version. This initialization or migration is done automatically when the container is started.

### circ-webapp

Once the webapp Docker image is built, we can run it in a container with the following command.

```sh
# See the section "Environment Variables" below for more information
# about the values listed here and their alternatives.
$ docker run --name webapp -d \
    --p 80:80 \
    -e SIMPLIFIED_PRODUCTION_DATABASE='postgresql://[username]:[password]@[host]:[port]/[database_name]' \
    ghcr.io/natlibfi/ekirjasto-circ-webapp:main
```

If the database and OpenSearch(OS) are running in containers, use the --link option to let the webapp docker container
to access them as bellow:

```sh
docker run \
--link pg --link os \
--name circ \
-e SIMPLIFIED_PRODUCTION_DATABASE='postgresql://[username]:[password]@[host]:[port]/[database_name]' \
-d -p 6500:80 \
ghcr.io/natlibfi/ekirjasto-circ-webapp:main
```

Navigate to `http://localhost/admin` in your browser to visit the web admin for the Circulation Manager. In the admin,
you can add or update configuration information. If you have not yet created an admin authorization protocol before,
you'll need to do that before you can set other configuration.

### circ-scripts

Once the scripts Docker image is built, we can run it in a container with the following command.

```sh
# See the section "Environment Variables" below for more information
# about the values listed here and their alternatives.
$ docker run --name scripts -d \
    -e TZ='YOUR_TIMEZONE_STRING' \
    -e SIMPLIFIED_PRODUCTION_DATABASE='postgresql://[username]:[password]@[host]:[port]/[database_name]' \
    ghcr.io/natlibfi/ekirjasto-circ-scripts:main
```

Using `docker exec -it scripts /bin/bash` in your console, navigate to `/var/log/simplified` in the container. After
5-20 minutes, you'll begin to see log files populate that directory.

### circ-exec

This image builds containers that will run a single script and stop. It's useful in conjunction with a tool like Amazon
ECS Scheduled Tasks, where you can run script containers on a cron-style schedule.

Unlike the `circ-scripts` image, which runs constantly and executes every possible maintenance script--whether or not
your configuration requires it--`circ-exec` offers more nuanced control of your Library Simplified Circulation Manager
jobs. The most accurate place to look for recommended jobs and their recommended frequencies is
[the existing `circ-scripts` crontab](https://github.com/NYPL-Simplified/circulation/blob/main/docker/services/simplified_crontab).

Because containers based on `circ-exec` are built, run their job, and are destroyed, it's important to configure an
external log aggregator if you wish to capture logs from the job.

```sh
# See the section "Environment Variables" below for more information
# about the values listed here and their alternatives.
$ docker run --name search_index_refresh -it \
    -e SIMPLIFIED_SCRIPT_NAME='refresh_materialized_views' \
    -e SIMPLIFIED_PRODUCTION_DATABASE='postgresql://[username]:[password]@[host]:[port]/[database_name]' \
    ghcr.io/natlibfi/ekirjasto-circ-exec:main
```

## Environment Variables

Environment variables can be set with the `-e VARIABLE_KEY='variable_value'` option on the `docker run` command.
`SIMPLIFIED_PRODUCTION_DATABASE` is the only required environment variable.

### `SIMPLIFIED_PRODUCTION_DATABASE`

_Required._ The URL of the production PostgreSQL database for the application.

### `SIMPLIFIED_TEST_DATABASE`

_Optional._ The URL of a PostgreSQL database for tests. This optional variable allows unit tests to be run in the
container.

### `TZ`

_Optional. Applies to `circ-scripts` only._ The time zone that cron should use to run scheduled scripts--usually the
time zone of the library or libraries on the circulation manager instance. This value should be selected according to
[Debian-system time zone options](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).
This value allows scripts to be run at ideal times.

### `UWSGI_PROCESSES`

_Optional._ The number of processes to use when running uWSGI. This value can be updated in `docker-compose.yml` or
added directly in `Dockerfile` under webapp stage. Defaults to 6.

### `UWSGI_THREADS`

_Optional._ The number of threads to use when running uWSGI. This value can be updated in `docker-compose.yml` or added
directly in `Dockerfile` under webapp stage. Defaults to 2.

## Building new images

If you plan to work with stable versions of the Circulation Manager, we strongly recommend using the latest stable
versions of circ-webapp and circ-scripts
[published to the GitHub Container Registry](https://github.com/orgs/NatLibFi/packages?repo_name=circulation).
However, there may come a time in development when you want to build Docker containers for a particular version of the
Circulation Manager. If so, please use the instructions below.

We recommend you install at least version 18.06 of the Docker engine.

### `baseimage`

All other images (`webapp`, `scripts`, `exec`) are built `FROM` a shared base image that bundles Python 3.12,
Poetry, the system libraries needed to compile the Python wheels (lxml, xmlsec, psycopg2, uwsgi), and a pre-installed
copy of the `main` + `pg` dependency groups. Building it is slow, so in CI it is built on a schedule and published to
the GitHub Container Registry as `ghcr.io/natlibfi/ekirjasto-circ-baseimage:latest`. The app image builds normally pull
that published image.

You only need to build the base image locally when you have changed dependencies (`pyproject.toml` / `poetry.lock`) and
want those changes baked into the base layer rather than re-installed on top of a stale base. Build it from the root of
the repository:

```sh
docker build \
  --file docker/Dockerfile.baseimage \
  --target baseimage \
  --tag ghcr.io/natlibfi/ekirjasto-circ-baseimage:latest \
  .
```

Tagging it as `...:latest` means the app image builds and `docker-compose-dev.yml` will pick up your local copy
automatically (that tag is the default value of the `BASE_IMAGE` build arg / `BUILD_BASE_IMAGE` compose variable). If
you would rather not shadow the published `:latest` tag, give it a different tag and point the build at it explicitly,
e.g. `--tag circ-baseimage:local` then `export BUILD_BASE_IMAGE=circ-baseimage:local` before building the app images.

> Note: even when an image is built on top of a stale base image, the final image build re-runs
> `poetry install --only main,pg` against the current `poetry.lock`, so the resulting image is always up to date. Building
> the base image fresh is purely an optimization that moves that work into the cached base layer.

### `webapp` and `scripts` images

Determine which image you would like to build and update the tag and `Dockerfile` listed below accordingly. Run the
build command from the root of the repository not the docker folder. Use `target` option to determine which docker
image to build as bellow:

```sh
docker build --tag circ --file docker/Dockerfile --target scripts .
```

See `docker/Dockerfile` for details.

Feel free to change the image tag as you like.

## Running the development stack

For local development, `docker-compose-dev.yml` (in the repository root) builds and runs the full stack — `webapp` and
`scripts` plus PostgreSQL, OpenSearch, MinIO (S3), pgadmin, the OpenSearch dashboard, and the data API. It uses
persistent host-mounted volumes for the database (`~/src/circ_psql_data`) and search index (`~/src/circ_os_data`) so
data survives container restarts.

### Build phase

The compose file does **not** build the base image; it pulls
`ghcr.io/natlibfi/ekirjasto-circ-baseimage:latest`. If you want the stack built on a locally built base image (for
example after a dependency change), build the base image first (see the `baseimage` section above), then build the app
images:

```sh
# Optional: build the base image fresh first (see "baseimage" above)

# Build webapp + scripts (and the local OpenSearch image) on top of the base image
docker compose -f docker-compose-dev.yml build

# Force a clean rebuild that ignores the layer cache
docker compose -f docker-compose-dev.yml build --no-cache
```

Relevant build-time variables (all optional, with sensible defaults):

| Variable | Purpose | Default |
| --- | --- | --- |
| `BUILD_BASE_IMAGE` | Base image to build the app images `FROM` | `ghcr.io/natlibfi/ekirjasto-circ-baseimage:latest` |
| `BUILD_PLATFORM` | Target platform passed to the build | host platform |
| `BUILD_CACHE_FROM` | Image used as a build cache source | `ghcr.io/natlibfi/ekirjasto-circ-webapp:main` |

### Run phase

```sh
# Build (if needed) and start everything in the background
docker compose -f docker-compose-dev.yml up --build -d
```

Database migrations run automatically when the containers start (Alembic, via `docker/startup/`). On a fresh database
the search index does not exist yet, so build it once via the `scripts` container:

```sh
docker exec -it scripts /bin/bash
../core/bin/run search_index_clear && ../core/bin/run search_index_refresh
```

Once running, the services are available at:

| Service | URL |
| --- | --- |
| App | <http://localhost:6500> |
| Admin | <http://localhost:6500/admin/> |
| pgadmin | <http://localhost:5050> (`admin@admin.com` / `root`) |
| MinIO console | <http://localhost:9001> (`palace` / `test123456789`) |
| OpenSearch | <http://localhost:9200> |
| OpenSearch Dashboards | <http://localhost:5601> |
| data-api | <http://localhost:8000> |
| PostgreSQL | `localhost:5432` (`palace` / `test` / `circ`) |

Before admin authentication will work you must set a real `ADMIN_EKIRJASTO_AUTHENTICATION_URL` in
`docker-compose-dev.yml` (it ships with a `"https://"` placeholder). To start completely fresh, stop the stack and
delete the persistent volume directories (`~/src/circ_psql_data`, `~/src/circ_os_data`).
