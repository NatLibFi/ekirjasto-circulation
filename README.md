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
    - [2. Python and dependencies to run tests (and the application locally)](#2-additional-setup-to-run-tests-and-the-application-locally)
        - [Python setup](#python-setup)
        - [Dependencies](#dependencies)
        - [venv - Virtual Environment](#venv---virtual-environment)
        - [Poetry](#poetry)
    - [3. Testing](#3-testing)
        - [Tox-Docker](#tox-docker)
        - [Running Tests](#running-tests)
        - [Coverage Reports](#coverage-reports)
    - [4. Further setup to run locally](#4-further-setup-to-run-locally)
        - [OpenSearch](#opensearch)
        - [Database](#database)
        - [Environment variables](#environment-variables)
        - [Storage Service](#storage-service)
        - [Reporting](#reporting)
        - [Logging](#logging)
        - [Firebase Cloud Messaging](#logging)
        - [OpenSearch Analytics (E-Kirjasto, Finland)](#opensearch-analytics-e-kirjasto-finland)
    - [5. Running the Application](#5-running-the-application)
    - [6. Installation Issues](#6-installation-issues)
- [The Admin Interface](#the-admin-interface)
    - [1. Access](#1-access)
    - [2. Creating A Library](#2-creating-a-library)
    - [3. Adding Collections](#3-adding-collections)
    - [4. Importing OPDS feeds](#4-importing-opds-feeds)
    - [5. Generating Search Indices](#5-generating-search-indices)
    - [6. Patron authentication](#6-patron-authentication)
    - [7. Troubleshooting](#7-troubleshooting)
    - [8. Sitewide Settings](#8-sitewide-settings)
- [Scheduled Jobs](#scheduled-jobs)
    - [1. Job Requirements](#job-requirements)
        - [hold_notifications](#hold_notifications)
        - [loan_notifications](#loan_notifications)
- [Code Style](#code-style)
    - [1. Pre-Commit Configuration](#1-pre-commit-configuration)
    - [2. Linters](#2-linters)
        - [Built in](#built-in)
        - [Black](#black)
        - [isort](#isort)
        - [autoflake](#autoflake)
- [Localization (i18n, l10n, flask-pybabel, managing translations)](#localization-i18n-l10n-flask-pybabel-managing-translations)
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

Check out the [Docker README](/docker/README.md) in the `/docker` directory for in-depth information on running and
developing the Circulation Manager locally with Docker, or for deploying the Circulation Manager with Docker.

For setting up a library with collections, you can skip over to section [The Admin Interface](#admin-interface).

### 2. Python and dependencies to run tests (and the application locally)

#### Python Setup

In order to run tests or the code outside Docker, you'll need to set up Python and a virtual environment. Macs should
have Python already installed.

#### Dependencies

Install the following required dependencies:

```sh
brew install pkg-config libffi
brew install tvuotila/libxmlsec1/libxmlsec1@1.2.37
brew install libjpeg
```

#### venv - Virtual environment

The codes uses Python 3.10 and 3.11. We mostly use 3.11, but install both versions (3.10 in case you want to run
tests against it):

```sh
brew install python@3.11
brew install python@3.10
```

Create a virtual environment that uses Python 3.11 and activate it:

```sh
python3.11 -m venv venv
source venv/bin/activate
```

#### Poetry

This project uses [poetry](https://python-poetry.org/) for dependency management.

Poetry can be installed using the command `curl -sSL https://install.python-poetry.org | python3 -` but at the moment,
Poetry version 1.8.3 works without problems. Install it:

```sh
brew install poetry@1.8.3
```

Run `poetry debug info` to check that Python 3.11 is used in Poetry and the envirnoment.

Then install dependencies:

```sh
poetry install
```

### 3. Testing

The Github Actions CI service runs the unit tests against Python 3.10, and 3.11 automatically using
[tox](https://tox.readthedocs.io/en/latest/).

Tox has an environment for each python version, the module being tested, and an optional `-docker` factor that will
automatically use docker to deploy service containers used for the tests. You can select the environment you would like
to test with the tox `-e` flag.

#### Tox-Docker

Tox-docker will take care of setting up all the service containers necessary to run the unit tests
and pass the correct environment variables to configure the tests to use these services. Using `tox-docker` is not
required, but it is the recommended way to run the tests locally, since it runs the tests in the same way they are run
on the Github Actions CI server. `tox-docker` is automatically included when installing the `ci` dependency group.

The docker functionality is included in a `docker` factor that can be added to the environment. To run an environment
with a particular factor you add it to the end of the environment.

#### Running Tests

There are two main test factors that run their test modules: `api` and `core`.

In practice, run `api` tests against Python 3.11:

```sh
tox -e py311-api-docker
```

or `core` tests:

```sh
tox -e py311-core-docker
```

You can add an optional `-v` switch for more verbose output.

A specific file can be run e.g.:

```sh
tox -e py311-api-docker -- tests/api/test_odl.py -v
```

or a specific test in a file using `-k` switch:

```sh
tox -e py311-api-docker -- tests/api/test_odl.py -k test_get_license_status_document_success
```

#### Coverage Reports

Code coverage is automatically tracked with [`pytest-cov`](https://pypi.org/project/pytest-cov/) when tests are run.
When the tests are run with github actions, the coverage report is automatically uploaded to
[codecov](https://about.codecov.io/) and the results are added to the relevant pull request.

When running locally, the results from each individual run can be collected and combined into an HTML report using
the `report` tox environment. This can be run on its own after running the tests, or as part of the tox environment
selection.

```sh
tox -e "py311-{core,api}-docker,report"
```

### 4. Further setup to run locally

#### OpenSearch

We recommend that you run OpenSearch with docker using the following docker commands:

```sh
docker run --name opensearch -d --rm -p 9200:9200 -e "discovery.type=single-node" -e "plugins.security.disabled=true" "opensearchproject/opensearch:1"
docker exec opensearch opensearch-plugin -s install analysis-icu
docker restart opensearch
```

#### Database

Using Docker:

```sh
docker run -d --name pg -e POSTGRES_USER=palace -e POSTGRES_PASSWORD=test -e POSTGRES_DB=circ -p 5432:5432 postgres:12
```

You can run `psql` in the container using the command

```sh
docker exec -it pg psql -U palace circ
```

Locally:

1. Download and install [Postgres](https://www.postgresql.org/download/) if you don't have it already.
2. Use the command `psql` to access the Postgresql client.
3. Within the session, run the following commands:

```sh
CREATE DATABASE circ;
CREATE USER palace with password 'test';
grant all privileges on database circ to palace;
```

#### Environment variables

To let the application know which database to use, set the `SIMPLIFIED_PRODUCTION_DATABASE` environment variable.

```sh
export SIMPLIFIED_PRODUCTION_DATABASE="postgresql://palace:test@localhost:5432/circ"
```

To let the application know which Opensearch instance to use, you can set the following environment variables:

- `PALACE_SEARCH_URL`: The url of the Opensearch instance (**required**).
- `PALACE_SEARCH_INDEX_PREFIX`: The prefix to use for the Opensearch indices. The default is `circulation-works`.
    This is useful if you want to use the same Opensearch instance for multiple CM (optional).
- `PALACE_SEARCH_TIMEOUT`: The timeout in seconds to use when connecting to the Opensearch instance. The default is `20`
  (optional).
- `PALACE_SEARCH_MAXSIZE`: The maximum size of the connection pool to use when connecting to the Opensearch instance.
  (optional).

```sh
export PALACE_SEARCH_URL="http://localhost:9200"
```

#### Storage Service

The application optionally uses a s3 compatible storage service to store files. To configure the application to use
a storage service, you can set the following environment variables:

- `PALACE_STORAGE_PUBLIC_ACCESS_BUCKET`: Required if you want to use the storage service to serve files directly to
  users. This is the name of the bucket that will be used to serve files. This bucket should be configured to allow
  public access to the files.
- `PALACE_STORAGE_ANALYTICS_BUCKET`: Required if you want to use the storage service to store analytics data.
- `PALACE_STORAGE_ACCESS_KEY`: The access key (optional).
    - If this key is set it will be passed to boto3 when connecting to the storage service.
    - If it is not set boto3 will attempt to find credentials as outlined in their
    [documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html#configuring-credentials).
- `PALACE_STORAGE_SECRET_KEY`: The secret key (optional).
- `PALACE_STORAGE_REGION`: The AWS region of the storage service (optional).
- `PALACE_STORAGE_ENDPOINT_URL`: The endpoint of the storage service (optional). This is used if you are using a
  s3 compatible storage service like [minio](https://min.io/).
- `PALACE_STORAGE_URL_TEMPLATE`: The url template to use when generating urls for files stored in the storage service
  (optional).
    - The default value is `https://{bucket}.s3.{region}.amazonaws.com/{key}`.
    - The following variables can be used in the template:
        - `{bucket}`: The name of the bucket.
        - `{key}`: The key of the file.
        - `{region}`: The region of the storage service.

#### Reporting

- `PALACE_REPORTING_NAME`: (Optional) A name used to identify the CM instance associated with generated reports.
- `SIMPLIFIED_REPORTING_EMAIL`: (Required) Email address of recipient of reports.

#### Logging

The application uses the [Python logging](https://docs.python.org/3/library/logging.html) module for logging. Optionally
logs can be configured to be sent to AWS CloudWatch logs. The following environment variables can be used to configure
the logging:

- `PALACE_LOG_LEVEL`: The log level to use for the application. The default is `INFO`.
- `PALACE_LOG_VERBOSE_LEVEL`: The log level to use for particularly verbose loggers. Keeping these loggers at a
  higher log level by default makes it easier to troubleshoot issues. The default is `WARNING`.
- `PALACE_LOG_CLOUDWATCH_ENABLED`: Enable / disable sending logs to CloudWatch. The default is `false`.
- `PALACE_LOG_CLOUDWATCH_REGION`: The AWS region of the CloudWatch logs. This must be set if using CloudWatch logs.
- `PALACE_LOG_CLOUDWATCH_GROUP`: The name of the CloudWatch log group to send logs to. Default is `palace`.
- `PALACE_LOG_CLOUDWATCH_STREAM`: The name of the CloudWatch log stream to send logs to. Default is
  `{machine_name}/{program_name}/{logger_name}/{process_id}`. See
  [watchtower docs](https://github.com/kislyuk/watchtower#log-stream-naming) for details.
- `PALACE_LOG_CLOUDWATCH_INTERVAL`: The interval in seconds to send logs to CloudWatch. Default is `60`.
- `PALACE_LOG_CLOUDWATCH_CREATE_GROUP`: Whether to create the log group if it does not exist. Default is `true`.
- `PALACE_LOG_CLOUDWATCH_ACCESS_KEY`: The access key to use when sending logs to CloudWatch. This is optional.
    - If this key is set it will be passed to boto3 when connecting to CloudWatch.
    - If it is not set boto3 will attempt to find credentials as outlined in their
    [documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html#configuring-credentials).
- `PALACE_LOG_CLOUDWATCH_SECRET_KEY`: The secret key to use when sending logs to CloudWatch. This is optional.

#### Firebase Cloud Messaging

For Firebase Cloud Messaging (FCM) support (e.g., for notifications), `one` (and only one) of the following should be set:

- `SIMPLIFIED_FCM_CREDENTIALS_JSON` - the JSON-format Google Cloud Platform (GCP) service account key or
- `SIMPLIFIED_FCM_CREDENTIALS_FILE` - the name of the file containing that key.

```sh
export SIMPLIFIED_FCM_CREDENTIALS_JSON='{"type":"service_account","project_id":"<id>", "private_key_id":"f8...d1", ...}'
```

...or...

```sh
export SIMPLIFIED_FCM_CREDENTIALS_FILE="/opt/credentials/fcm_credentials.json"
```

The FCM credentials can be downloaded once a Google Service account has been created.
More details in the [FCM documentation](https://firebase.google.com/docs/admin/setup#set-up-project-and-service-account)

#### OpenSearch Analytics (E-Kirjasto, Finland)

OpenSearch analytics can be enabled via the following environment variables:

- PALACE_OPENSEARCH_ANALYTICS_ENABLED: A boolean value to disable or enable OpenSearch analytics. The default is false.
- PALACE_OPENSEARCH_ANALYTICS_URL: The url of your OpenSearch instance, eg. "http://localhost:9200"
- PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX: The prefix of the event index name, eg. "circulation-events"

Opensearch Dashboard can be accessed in `http://localhost:5601`.

### 5. Running the Application

The `poetry` tool should be executed under a virtual environment.
For brevity, these instructions assume that all shell commands will be executed within a virtual environment which, in
our case, in the venv we created earlier.

```sh
poetry run python app.py
```

Check that there is now a web server listening on port `6500`:

```sh
curl http://localhost:6500/
```

### 6. Installation Issues

When running the `poetry install ...` command, you may run into installation issues. On newer macos machines, you may
encounter an error such as:

```sh
error: command '/usr/bin/clang' failed with exit code 1
  ----------------------------------------
  ERROR: Failed building wheel for xmlsec
Failed to build xmlsec
ERROR: Could not build wheels for xmlsec which use PEP 517 and cannot be installed directly
```

This typically happens after installing packages through brew and then running the `pip install` command.

This [blog post](https://mbbroberg.fun/clang-error-in-pip/) explains and shows a fix for this issue. Start by trying
the `xcode-select --install` command. If it does not work, you can try adding the following to your `~/.zshrc` or
`~/.bashrc` file, depending on what you use:

```sh
export CPPFLAGS="-DXMLSEC_NO_XKMS=1"
```

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

Navigating to `http://localhost:6500/` should now show an OPDS feed containing various books:

![Feed](.github/readme/feed.png)

### 6. Patron authentication

For patrons to access the service, configure authentication: `System Configuration → Patron Authentication`. In our case,
select _E-kirjasto API environment: Development_ and attach the newly created library to the service.

### 7. Troubleshooting

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

If books arent' showing up in the admin UI or the applications, running lanes related scripts might help:

```sh
../core/bin/run update_lane_size
../core/bin/run update_custom_list_size
../core/bin/run reset_lanes # This should compile the default lanes from scratch but removes any custom lanes
```

### 8. Sitewide Settings

Some settings have been provided in the admin UI that configure or toggle various functions of the Circulation Manager.
These can be found at `/admin/web/config/SitewideSettings` in the admin interface.

#### Push Notification Status

This setting is a toggle that may be used to turn on or off the ability for the the system
to send the Loan and Hold reminders to the mobile applications.

## Scheduled Jobs

All jobs are scheduled via `cron`, as specified in the `docker/services/simplified_crontab` file.
This includes all the import and reaper jobs, as well as other necessary background tasks, such as maintaining
the search index and feed caches.

### Job Requirements

#### hold_notifications

Requires one of [the Firebase Cloud Messaging credentials environment variables (described above)](#firebase-cloud-messaging)
to be present and non-empty.
In addition, the site-wide `PUSH_NOTIFICATIONS_STATUS` setting must be either `unset` or `true`.

#### loan_notifications

Requires one of [the Firebase Cloud Messaging credentials environment variables (described above](#firebase-cloud-messaging)
to be present and non-empty.
In addition, the site-wide `PUSH_NOTIFICATIONS_STATUS` setting must be either `unset` or `true`.

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

## Localization (i18n, l10n, flask-pybabel, managing translations)

When adding new translations (with `gettext()` or alias `_()`), make sure to
update the translation files with:

```bash
bin/util/collect_translations
```

Here's what the script does:

1) Collects and generates translations from source files with a custom script.
2) Creates new translation templates (`*.pot`) with `pybabel extract`
3) Updates existing translation files (`*.po`) with `pybabel update`

## PyInstrument

This profiler uses [PyInstrument](https://pyinstrument.readthedocs.io/en/latest/) to profile the code.

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
