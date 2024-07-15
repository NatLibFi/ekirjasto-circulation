# Makefile for setting up pyenv, pyenv-virtualenv, and installing dependencies with Poetry

.PHONY: install_all install_dependencies dependencies install_libxmlsec1 install_env install_pyenv \
	install_pyenv_virtualenv setup_shell install_python create_virtualenv install_packages run setup_env_variables \
	run_python_app start_docker stop_docker rebuild_docker clean

install_all: install_dependencies install_env


# SECTION 1: Install dependencies

DEPENDENCIES := pkg-config libffi libjpeg

install_dependencies: dependencies install_libxmlsec1

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


# SECTION 2: Install and set up the virtual environment with needed dependiencies

# The Python version and virtual environment name
PYTHON_VERSION=3.11.1
VENV_NAME=circ-dev

install_env: install_pyenv install_pyenv_virtualenv setup_shell install_python create_virtualenv install_packages

install_pyenv:
	@if ! command -v pyenv >/dev/null 2>&1; then \
		echo "Installing pyenv..."; \
		brew install pyenv; \
		echo 'eval "$$(pyenv init --path)"' >> ~/.zshrc; \
		eval "$$(pyenv init --path)"; \
	else \
		echo "pyenv is already installed."; \
	fi

install_pyenv_virtualenv:
	@if ! pyenv commands | grep virtualenv >/dev/null 2>&1; then \
		echo "Installing pyenv-virtualenv..."; \
		brew install pyenv-virtualenv; \
		echo 'eval "$$(pyenv virtualenv-init -)"' >> ~/.zshrc; \
		eval "$$(pyenv virtualenv-init -)"; \
	else \
		echo "pyenv-virtualenv is already installed."; \
	fi

setup_shell:
	@echo "Setting up shell configuration..."
	@grep -qxF 'export PYENV_ROOT="$(HOME)/.pyenv"' $(HOME)/.zshrc || echo 'export PYENV_ROOT="$(HOME)/.pyenv"' >> $(HOME)/.zshrc
	@grep -qxF 'export PATH="$(PYENV_ROOT)/bin:$(PATH)"' $(HOME)/.zshrc || echo 'export PATH="$(PYENV_ROOT)/bin:$(PATH)"' >> $(HOME)/.zshrc
	@grep -qxF 'eval "$$(pyenv init --path)"' $(HOME)/.zshrc || echo 'eval "$$(pyenv init --path)"' >> $(HOME)/.zshrc
	@grep -qxF 'eval "$$(pyenv init -)"' $(HOME)/.zshrc || echo 'eval "$$(pyenv init -)"' >> $(HOME)/.zshrc
	@grep -qxF 'eval "$$(pyenv virtualenv-init -)"' $(HOME)/.zshrc || echo 'eval "$$(pyenv virtualenv-init -)"' >> $(HOME)/.zshrc
	@echo "Please restart your shell or run 'source $(HOME)/.zshrc' to apply changes."

install_python:
	@echo "Installing Python $(PYTHON_VERSION)..."
	pyenv install -s $(PYTHON_VERSION)

create_virtualenv:
	@echo "Creating virtual environment $(VENV_NAME)..."
	pyenv virtualenv $(PYTHON_VERSION) $(VENV_NAME)

install_packages:
	@echo "Installing packages with Poetry..."
	pyenv activate $(VENV_NAME)
	poetry install


# SECTION 3: Run the python application and the containers in development mode.

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
