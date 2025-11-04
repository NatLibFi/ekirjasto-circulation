# E-kirjasto Circulation Manager

[![Test & Build](https://github.com/NatLibFi/ekirjasto-circulation/actions/workflows/test-build.yml/badge.svg)](https://github.com/NatLibFi/ekirjasto-circulation/actions/workflows/test-build.yml)

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Imports: isort](https://img.shields.io/badge/%20imports-isort-%231674b1?style=flat&labelColor=ef8336)](https://pycqa.github.io/isort/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
![Python: 3.10,3.11](https://img.shields.io/badge/Python-3.10%20|%203.11-blue)
This is the E-kirjasto fork of the [The Palace Project](https://thepalaceproject.org) Palace Manager (which is a fork of
[Library Simplified](http://www.librarysimplified.org/) Circulation Manager).

## Installation

Docker images created from this code will be available at:

- [ekirjasto-circ-webapp](https://github.com/NatLibFi/circulation/pkgs/container/ekirjasto-circ-webapp)
- [ekirjasto-circ-scripts](https://github.com/NatLibFi/circulation/pkgs/container/ekirjasto-circ-scripts)
- [ekirjasto-circ-exec](https://github.com/NatLibFi/circulation/pkgs/container/ekirjasto-circ-exec)

Docker images are the preferred way to deploy this code in a production environment.

## Git Branch Workflow

The default branch is `main` and that's the working branch that should be used when branching off for bug fixes or new
features.

Commits to main can only be done by creating a PR.

## Continuous Integration

This project runs all the unit tests through Github Actions for new pull requests and when merging into the default
`main` branch. The relevant file can be found in `.github/workflows/test-build.yml`. When contributing updates or
fixes, it's required for the test Github Action to pass for all Python 3 environments. Run the `tox` command locally
before pushing changes to make sure you find any failing tests before committing them.

For each push to a branch, CI also creates a docker image for the code in the branch. These images can be used for
testing the branch, or deploying hotfixes.

To install the tools used by CI run:

```sh
poetry install --only ci
```

## Table of Contents

- [Setup](#setup)
    - [1. Set up and run the application with Docker Compose](#1-set-up-and-run-the-application-with-docker-compose)
- [The Admin Interface](#the-admin-interface)
    - [1. Access](#1-access)
    - [2. Creating A Library](#2-creating-a-library)
    - [3. Adding Collections](#3-adding-collections)
    - [4. Importing OPDS feeds](#4-importing-opds-feeds)
    - [5. Generating Search Indices](#5-generating-search-indices)
    - [6. Patron authentication](#6-patron-authentication)
    - [7. Troubleshooting](#7-troubleshooting)
    - [8. Sitewide Settings](#8-sitewide-settings)
    - [9. Scheduled Jobs](#9-scheduled-jobs)
        - [hold_notifications](#hold_notifications)
        - [loan_notifications](#loan_notifications)
    - [10. Informational scrits](#10-informational-scripts)
- [Testing](#testing)
    - [1. venv - Virtual environment](#1-venv---Virtual-environment)
    - [2. Poetry](#2-Poetry)
    - [3. Tox-Docker](#3-Running-tox-inside-Docker)
    - [4. Running Tests](#4-Run-the-API-or-core-suites)
    - [5. Run an individual file or test](#5-Run-an-individual-file-or-test)
- [Localization (flask-pybabel, Transifex)](#localization-flask-pybabel-transifex)
- [Technology overview](#technology-overview)
- [Code Style](#code-style)
    - [1. Pre-Commit Configuration](#3-pre-commit-configuration)
    - [2. Linters](#4-linters)
        - [Built in](#built-in)
        - [Black](#black)
        - [isort](#isort)
        - [autoflake](#autoflake)
    - [3. Mypy](#5-Mypy)
- [PyInstrument](#pyinstrument)
    - [Profiling tests suite](#profiling-tests-suite)
    - [Environment Variables](#environment-variables-1)

## Setup

### 1. Set up and run the application with Docker Compose

To quickly set up a development environment, we include a [docker-compose-dev.yml](./docker-compose-dev.yml)
file. This docker-compose file, will build the webapp and scripts containers from your local repository, and start
those containers as well as all the necessary service containers.

But first, install docker if not yet installed:

```sh
brew install --cask docker
```

Start the Docker app and then check it's running:

```sh
docker ps
```

Add the url for `ADMIN_EKIRJASTO_AUTHENTICATION_URL` in the `docker-compose-dev.yml` file. Then build the containers by
running the following command:

```sh
docker compose -f docker-compose-dev.yml up --build -d
```

There is now a web server listening on port `6500`:

```sh
curl http://localhost:6500/
```

This is all you need for day-to-day development.

Check out the not-so-up-to-date [Docker README](/docker/README.md) in the `/docker` directory for in-depth information
on running and developing the Circulation Manager locally with Docker, or for deploying the Circulation Manager with
Docker.

Now that you have a running application, you need to set up a library, add collections, etc.

If you want to run the application locally without Docker, check out-of-date `docs/local_setup.md`.

## The Admin Interface

### 1. Access

By default, the application is configured to provide a built-in version of the [admin web interface](https://github.com/NatLibFi/ekirjasto-circulation-admin).
The admin interface can be accessed by visiting the `/admin` endpoint:

```sh
# On Linux
xdg-open http://localhost:6500/admin/

# On MacOS
open http://localhost:6500/admin/
```

If no existing users are configured (which will be the case if this is a fresh instance of the application), the
admin interface will prompt you to specify an email address and password that will be used for subsequent logins.
Extra users can be configured later.

### 2. Creating A Library

Navigate to `System Configuration → Libraries` and click _Create new library_. You will be prompted to enter various
details such as the name of the library, a URL, and more. For example, the configuration for a hypothetical
library, _Test Library_, might look like this:

![.github/readme/library.png](.github/readme/library.png)

Note that the _Patron support email address_ will appear in OPDS feeds served by the application, so make sure
that it is an email address you are happy to make public.

At this point, the _library_ exists but does not contain any _collections_ and therefore won't be of much use to anyone.

### 3. Adding Collections

Navigate to `System Configuration → Collections` and click _Create new collection_. You will prompted to enter
details that will be used to source the data for the collection. A good starting point, for testing purposes,
is to use an open access OPDS feed as a data source. The
[Open Bookshelf](https://palace-bookshelf-opds2.dp.la/v1/publications) is a good example of such a feed. Enter the
following details:

![.github/readme/collection.png](.github/readme/collection.png)

Note that we associate the collection with the newly created library by selecting it in the `Libraries` drop-down.
A collection can be associated with any number of libraries.

### 4. Importing OPDS feeds

It's now necessary to tell the application to start importing books from the OPDS feed. When the application is
running inside a Docker image, the image is typically configured to execute various import operations on a regular
schedule using `cron`. Because we're running the application from the command-line for development purposes, we
need to execute these operations ourselves manually. Access the `scripts` container and then execute
`odl2_import_monitor` (E-kirjasto receveives ODL2 feeds) script:

```sh
docker exec -it scripts /bin/bash
../core/bin/run odl2_import_monitor
```

You can view the import log e.g.

```sh
less /var/log/simplified/odl2_import_monitor.log
```

The command will cause the application to crawl the configured OPDS feed and import every book in it. Importing 100
books takes a few minutes while thousands take almost an hour. Please wait for the import to complete before continuing!

When the import has completed, the books are imported but no OPDS feeds will have been generated, and no search
service has been configured.

### 5. Generating Search Indices

As with the collection [configured earlier](#adding-collections), the application depends upon various operations
being executed on a regular schedule to generate search indices. You can wait for these scheluded jobs to be run or run
them manually:

```sh
../core/bin/run search_index_clear
../core/bin/run search_index_refresh
```

Neither of the commands will produce any output if the operations succeed.

### 6. Patron authentication

For patrons to access the service, configure authentication: `System Configuration → Patron Authentication`. In our case,
select _E-kirjasto API environment: Development_ and attach the newly created library to the service.

Navigating to `http://localhost:6500/` should now show an OPDS feed containing various books:

![Feed](.github/readme/feed.png)

If not, see [7. Troubleshooting](#7-troubleshooting).

### 7. Troubleshooting

If books arent' showing up in the admin UI or the applications, running lanes related scripts might help:

```sh
../core/bin/run update_lane_size
../core/bin/run update_custom_list_size
../core/bin/run reset_lanes # This should compile the default lanes from scratch but removes any custom lanes
```

The `./bin/repair/where_are_my_books` command can produce output that may indicate why books are not appearing
in OPDS feeds. A working, correctly configured installation, at the time of writing, produces output such as this:

```sh
(circ) $ ./bin/repair/where_are_my_books
Checking library Hazelnut Peak
 Associated with collection Palace Bookshelf.
 Associated with 171 lanes.

0 feeds in cachedfeeds table, not counting grouped feeds.

Examining collection "Palace Bookshelf"
 7838 presentation-ready works.
 0 works not presentation-ready.
 7824 works in the search index, expected around 7838.
```

We can see from the above output that the vast majority of the books in the _Open Bookshelf_ collection
were indexed correctly.

### 8. Sitewide Settings

Some settings have been provided in the admin UI that configure or toggle various functions of the Circulation Manager.
These can be found at `/admin/web/config/SitewideSettings` in the admin interface.

#### Push Notification Status

This setting is a toggle that may be used to turn on or off the ability for the the system
to send the Loan and Hold reminders to the mobile applications.

### 9. Scheduled Jobs

All jobs are scheduled via `cron`, as specified in the `docker/services/cron/cron.d/circulation` file.
This includes all the import and reaper jobs, as well as other necessary background tasks, such as maintaining
the search index and feed caches.

#### hold_notifications

Requires one of [the Firebase Cloud Messaging credentials environment variables (described above)](#firebase-cloud-messaging)
to be present and non-empty.
In addition, the site-wide `PUSH_NOTIFICATIONS_STATUS` setting must be either `unset` or `true`.

#### loan_notifications

Requires one of [the Firebase Cloud Messaging credentials environment variables (described above](#firebase-cloud-messaging)
to be present and non-empty.
In addition, the site-wide `PUSH_NOTIFICATIONS_STATUS` setting must be either `unset` or `true`.

### 10. Informational scripts

There are some useful scripts in `bin/informational` you can run in the `scripts` container:

- `explain`: Details of a book including bibliographical details, licenses, loans, holds, genres, etc.

```sh
../core/bin/run informational/explain --identifier-type="ISBN" 9780008621506
```

- `license_report`: License pool and license details with checkouts, loans, holds queue, etc. Helps to identify issues
with failing loans.

```sh
../core/bin/run informational/license_report
```

## Testing

The Github Actions CI service runs the unit tests against Python 3.10, and 3.11 automatically using
[tox](https://tox.readthedocs.io/en/latest/).

Tox has an environment for each python version, the module being tested, and an optional `-docker` factor that will
automatically use docker to deploy service containers used for the tests. You can select the environment you would like
to test with the tox `-e` flag.

for running the tests locally, you'll need a `venv`.

### 1. venv - Virtual environment

The codes uses Python 3.10 and 3.11. We mostly use 3.11.

```sh
brew install python@3.11
```

Create a virtual environment that uses Python 3.11 and activate it:

```sh
python3.11 -m venv venv
source venv/bin/activate
```

### 2. Poetry

This project uses [poetry](https://python-poetry.org/) for dependency management.

Poetry can be installed using the command `curl -sSL https://install.python-poetry.org | python3 -` but at the moment,
Poetry version 1.8.3 works without problems. Install it:

```sh
brew install poetry@1.8.5
```

Run `poetry debug info` to check that Python 3.11 is used in Poetry and the envirnoment.

Then install dependencies:

```sh
poetry install
```

### 3. Running tox inside Docker

Use the `scripts/tox-docker.sh` helper to run tox in the container defined in `docker-compose-tox.yml`. The tests are
run using `Python 3.11`. Docker Desktop (macOS/Windows) or a running Docker Engine (Linux) is required.

Build the image (only needed after dependency or Dockerfile changes):

```sh
./scripts/tox-docker.sh build
```

### 4. Run the API or core suites

```sh
./scripts/tox-docker.sh api
./scripts/tox-docker.sh core
```

Run both suites in one go:

```sh
./scripts/tox-docker.sh all
```

### 5. Run an individual file or test

When you skip the `api`/`core` subcommand the script infers the environment from the test paths and forwards any
pytest options after an optional `--`:

```sh
./scripts/tox-docker.sh tests/api/test_odl2.py -k test_import
./scripts/tox-docker.sh tests/core/test_circulation_data.py::TestCirculationData
```

The script wraps `docker compose -f docker-compose-tox.yml run --rm tox` so the repository mounts into `/workspace` and
the host Docker daemon remains available for `tox-docker`. Export `LOCAL_UID` and `LOCAL_GID` before invoking the script
if you need container file ownership to match your host user.

## Localization (flask-pybabel, Transifex)

When adding new translations (with `gettext()` or alias `_()`), the new strings are added by a script. For the script to
work, the backend should be running, and you need to export the following environment variables:

```bash
export SIMPLIFIED_PRODUCTION_DATABASE="postgresql://palace:test@localhost:5432/circ"
export PALACE_SEARCH_URL="http://opensearch:9200"
```

Then run the script:

```bash
bin/util/collect_translations
```

Here's what the script does:

1) Collects and generates translations from source files with a custom script.
2) Creates new translation templates (`*.pot`) with `pybabel extract`
3) Updates existing translation files (`*.po`) with `pybabel update`

Check the generated `.po` files and make the following changes if you see any:
- In the English file, if `msgstr` is empty, replace it with the same string as the `msgid` string.
- In the Finnish and Swedish files, if the `msgstr` is empty, replace with a placeholder string, e.g. "TRANSLATE".
- Remove any `fuzzy` attributes or they won't show up in Transifex.

Make PR, and when it's merged to main, the translations will be uploaded to Transifex.

Once all strings have been translated and reviewed in Transifex, Transifex will automatically create a PR in Github and
commit the updated files.

## Technology overview

E-kirjasto backend - though gone through many changes since being forked from the Palace project - is based on the
following technologies:

- [Data model](https://lyrasis.notion.site/Data-Model-6bc7de7799c543388d3cb206b72a8dad)
- [Search](https://lyrasis.notion.site/Search-6f24d9ffe0af45cc8e4e1726d535060b)

## Code Style

Code style on this project is linted using [pre-commit](https://pre-commit.com/). This python application is included
in our `pyproject.toml` file, so if you have the applications requirements installed it should be available. pre-commit
is run automatically on each push and PR by our [CI System](#continuous-integration).

Run it manually on all files before pushing to the repository:

```sh
pre-commit run --all-files
```

You can also set it up, so that it runs automatically for you on each commit. Running the command `pre-commit install`
will install the pre-commit script in your local repositories git hooks folder, so that pre-commit is run automatically
on each commit.

### 1. Pre-Commit Configuration

The pre-commit configuration file is named [`.pre-commit-config.yaml`](.pre-commit-config.yaml). This file configures
the different lints that pre-commit runs.

### 2. Linters

#### Built in

Pre-commit ships with a [number of lints](https://pre-commit.com/hooks.html) out of the box, we are configured to use:

- `trailing-whitespace` - trims trailing whitespace.
- `end-of-file-fixer` - ensures that a file is either empty, or ends with one newline.
- `check-yaml` - checks yaml files for parseable syntax.
- `check-json` - checks json files for parseable syntax.
- `check-ast` - simply checks whether the files parse as valid python.
- `check-shebang-scripts-are-executable` - ensures that (non-binary) files with a shebang are executable.
- `check-executables-have-shebangs` - ensures that (non-binary) executables have a shebang.
- `check-merge-conflict` - checks for files that contain merge conflict strings.
- `check-added-large-files` - prevents giant files from being committed.
- `mixed-line-ending` - replaces or checks mixed line ending.

#### Black

We lint using the [black](https://black.readthedocs.io/en/stable/) code formatter, so that all of our code is formatted
consistently.

#### isort

We lint to make sure our imports are sorted and correctly formatted using [isort](https://pycqa.github.io/isort/). Our
isort configuration is stored in our [tox.ini](tox.ini) which isort automatically detects.

#### autoflake

We lint using [autoflake](https://pypi.org/project/autoflake/) to flag and remove any unused import statement. If an
unused import is needed for some reason it can be ignored with a `#noqa` comment in the code.

### 3. Mypy

Mypy is also is run automatically on each push and PR so be sure to run it locally for checking any static typing
issues.

```sh
mypy
```

## PyInstrument

This profiler uses [PyInstrument](https://pyinstrument.readthedocs.io/en/latest/) to profile the code.
(Currently not enabled in E-kirjasto.)

### Profiling tests suite

PyInstrument can also be used to profile the test suite. This can be useful to identify slow tests, or to identify
performance regressions.

To profile the core test suite, run the following command:

```sh
pyinstrument -m pytest --no-cov tests/core/
```

To profile the API test suite, run the following command:

```sh
pyinstrument -m pytest --no-cov tests/api/
```

### Environment Variables

- `PALACE_PYINSTRUMENT`: Profiling will the enabled if this variable is set. The saved profile data will be available at
  path specified in the environment variable.

    - The profile data will have the extension `.pyisession`.
    - The data can be accessed with the
      [`pyinstrument.session.Session` class](https://pyinstrument.readthedocs.io/en/latest/reference.html#pyinstrument.session.Session).
    - Example code to print details of the gathered statistics:

    ```python
    import os
    from pathlib import Path

    from pyinstrument.renderers import HTMLRenderer
    from pyinstrument.session import Session

    path = Path(os.environ.get("PALACE_PYINSTRUMENT"))
    for file in path.glob("*.pyisession"):
        session = Session.load(file)
        renderer = HTMLRenderer()
        renderer.open_in_browser(session)
    ```
