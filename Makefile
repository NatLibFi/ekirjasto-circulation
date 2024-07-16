# Makefile for setting up a virtual enviroment and running the application.

.PHONY: install dependencies install_libxmlsec1 

# SECTION 1: Install dependencies

DEPENDENCIES := pkg-config libffi libjpeg poetry pyenv pyenv-virtualenv

install: dependencies install_libxmlsec1

dependencies: $(DEPENDENCIES)

$(DEPENDENCIES):
	@if ! brew list $@ >/dev/null 2>&1; then \
		echo "Installing $@..."; \
		brew install $@; \
	else \
		echo "$@ is already installed."; \
	fi

install_libxmlsec1:
	@if ! brew list libxmlsec1@1.2.37 >/dev/null 2>&1; then \
		echo "Installing libxmlsec1@1.2.37..."; \
		brew tap tvuotila/libxmlsec1; \
		brew install tvuotila/libxmlsec1/libxmlsec1@1.2.37; \
	else \
		echo "libxmlsec1@1.2.37 is already installed."; \
	fi

# SECTION 2: Set up the virtual environment with Python packages

.PHONY: venv

# The Python version and virtual environment name
PYTHON_VERSION=3.11.1
VENV_NAME=circ-311

venv:
	@echo "Installing Python $(PYTHON_VERSION)..."
	pyenv install -s $(PYTHON_VERSION)
	@echo "Creating virtual environment $(VENV_NAME)..."
	pyenv virtualenv $(PYTHON_VERSION) $(VENV_NAME)

# SECTION 3: Run the python application and the containers in development mode.

.PHONY: run setup_env_variables run_python_app start_docker stop_docker rebuild_docker clean

# Define environment variables, change them to whatever you need
export ADMIN_EKIRJASTO_AUTHENTICATION_URL=https://localhost # This should be changed to the actual one!
export PALACE_SEARCH_URL=http://localhost:9200
export SIMPLIFIED_PRODUCTION_DATABASE=postgresql://palace:test@localhost:5432/circ

PYTHON_APP = app.py

# Docker compose file, change filename to whatever you need
DOCKER_COMPOSE_FILE = docker-compose-dev.yml

run: setup_env_variables rebuild_docker run_python_app

# Target to set up environment (exporting environment variables)
setup_env_variables:
	@echo "Setting up environment..."
	@export ADMIN_EKIRJASTO_AUTHENTICATION_URL=${ADMIN_EKIRJASTO_AUTHENTICATION_URL}
	@export PALACE_SEARCH_URL=${PALACE_SEARCH_URL}
	@export SIMPLIFIED_PRODUCTION_DATABASE=${SIMPLIFIED_PRODUCTION_DATABASE}
	@echo "Environment variables set: ADMIN_EKIRJASTO_AUTHENTICATION_URL=${ADMIN_EKIRJASTO_AUTHENTICATION_URL}, \
	PALACE_SEARCH_URL=${PALACE_SEARCH_URL}, SIMPLIFIED_PRODUCTION_DATABASE=${SIMPLIFIED_PRODUCTION_DATABASE}"

# Target to run the Python application instead in a Docker container
run_python_app:
	@echo "Starting Python application..."
	@poetry run python ${PYTHON_APP}

start_docker:
	@echo "Starting Docker containers..."
	@docker-compose -f ${DOCKER_COMPOSE_FILE} up -d

stop_docker:
	@echo "Stopping Docker containers..."
	@docker-compose -f ${DOCKER_COMPOSE_FILE} down

rebuild_docker:
	@echo "Rebuilding Docker images ..."
	@docker-compose -f ${DOCKER_COMPOSE_FILE} build --no-cache
	@docker-compose -f ${DOCKER_COMPOSE_FILE} up -d

# Define the path where the PostgreSQL data is stored: Check your docker-compose-dev.yml file to see the location.
POSTGRES_DATA = ../circ-postgres-postrelease

# Clean target to remove any generated files or containers (if needed)
clean: stop_docker
	@echo "Cleaning up..."
	@if [ -d "${POSTGRES_DATA}" ]; then \
		echo "Deleting directory: ${POSTGRES_DATA}"; \
		rm -rf ${POSTGRES_DATA}; \
		echo "Deleted directory: ${POSTGRES_DATA}"; \
	else \
		echo "Directory ${POSTGRES_DATA} does not exist."; \
	fi
