# Palace locally run setup guide (outdated)

- [1. Python and dependencies to run tests (and the application locally)](#2-additional-setup-to-run-tests-and-the-application-locally)
    - [Python setup](#python-setup)
    - [Dependencies](#dependencies)
    - [venv - Virtual Environment](#venv---virtual-environment)
    - [Poetry](#poetry)
    - [OpenSearch](#opensearch)
    - [Database](#database)
    - [Environment variables](#environment-variables)
    - [Storage Service](#storage-service)
    - [Reporting](#reporting)
    - [Logging](#logging)
    - [Firebase Cloud Messaging](#logging)
    - [OpenSearch Analytics (E-Kirjasto, Finland)](#opensearch-analytics-e-kirjasto-finland)
- [2. Running the Application](#2-running-the-application)
- [3. Installation Issues](#3-installation-issues)

## 1. Python and dependencies to application locally

### Python Setup

In order to run tests or the code outside Docker, you'll need to set up Python and a virtual environment. Macs should
have Python already installed.

### Dependencies

Install the following required dependencies:

```sh
brew install pkg-config libffi
brew install tvuotila/libxmlsec1/libxmlsec1@1.2.37 # Does not work anymore
brew install libjpeg
```

### venv - Virtual environment

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

### Poetry

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

### OpenSearch

We recommend that you run OpenSearch with docker using the following docker commands:

```sh
docker run --name opensearch -d --rm -p 9200:9200 -e "discovery.type=single-node" -e "plugins.security.disabled=true" "opensearchproject/opensearch:1"
docker exec opensearch opensearch-plugin -s install analysis-icu
docker restart opensearch
```

### Database

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

### Environment variables

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

### Storage Service

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

### Reporting

- `PALACE_REPORTING_NAME`: (Optional) A name used to identify the CM instance associated with generated reports.
- `SIMPLIFIED_REPORTING_EMAIL`: (Required) Email address of recipient of reports.

### Logging

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

### Firebase Cloud Messaging

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

### OpenSearch Analytics (E-Kirjasto, Finland)

OpenSearch analytics can be enabled via the following environment variables:

- PALACE_OPENSEARCH_ANALYTICS_ENABLED: A boolean value to disable or enable OpenSearch analytics. The default is false.
- PALACE_OPENSEARCH_ANALYTICS_URL: The url of your OpenSearch instance, eg. "http://localhost:9200"
- PALACE_OPENSEARCH_ANALYTICS_INDEX_PREFIX: The prefix of the event index name, eg. "circulation-events"

Opensearch Dashboard can be accessed in `http://localhost:5601`.

## 2. Running the Application

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

## 3. Installation Issues

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
