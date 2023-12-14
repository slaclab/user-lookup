CONTAINER_RT?=podman
REPO?=slaclab
POD?=user-lookup
TAG?=latest

PYTHON_DIR?=~/.local/miniconda3/bin
PYTHON_BIN?=$(PYTHON_DIR)/python3
PIP_BIN?=$(PYTHON_DIR)/pip3
VENV_BIN?=$(PYTHON_DIR)/activate
UVICORN?=$(PYTHON_DIR)/uvicorn

LDAP_SERVER ?= ldaps://ldap-ad.slac.stanford.edu:636 
LDAP_USER_BASEDN ?= DC=win,DC=slac,DC=Stanford,DC=edu
LDAP_BIND_USERNAME ?= CN=osmaint,OU=Service-Accounts,OU=SCS,DC=win,DC=slac,DC=stanford,DC=edu
SECRET_PATH ?= secret/tid/scs/osmaint

APP_MOUNT ?= -v ./:/app
DEBUG ?= "1"

secrets:
	mkdir -p etc/.secrets
	vault kv get --field=password -format=table $(SECRET_PATH) > etc/.secrets/ldap.password
	vault kv get --field=token -format=table secret/tid/coact-dev/urawi > etc/.secrets/urawi.token

build:
	$(CONTAINER_RT) build -t $(REPO)/$(POD):$(TAG) .

push:
	$(CONTAINER_RT) push $(REPO)/$(POD):$(TAG)

containers: build push

virtualenv:
	$(PYTHON_BIN) -m venv .

pip:
	$(PYTHON_BIN) -m pip install --upgrade pip
	source $(VENV_BIN) && $(PIP_BIN) install -r requirements.txt

devenv: virtualenv pip

delenv:
	rm -rf bin lib lib64 pip-selfcheck.json pyvenv.cfg __pycache__

start:
	source $(VENV_BIN) && $(UVICORN) main:app --host 0.0.0.0 --reload

start-container:
	$(CONTAINER_RT) run -p 8000:8000 $(APP_MOUNT) -e DEBUG=$(DEBUG) -e URAWI_TOKEN=$(shell cat etc/.secrets/urawi.token) -e SOURCE_LDAP_SERVER=$(LDAP_SERVER) -e SOURCE_LDAP_BIND_USERNAME=$(LDAP_BIND_USERNAME) -e SOURCE_LDAP_BIND_PASSWORD='$(shell cat etc/.secrets/ldap.password)' -e SOURCE_LDAP_USER_BASEDN=$(LDAP_USER_BASEDN) -it $(REPO)/$(POD):$(TAG) 
