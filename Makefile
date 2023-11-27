CONTAINER_RT?=podman
REPO?=slaclab
POD?=user-lookup
TAG?=latest

PYTHON_DIR?=~/.local/miniconda3/bin
PYTHON_BIN?=$(PYTHON_DIR)/python3
PIP_BIN?=$(PYTHON_DIR)/pip3
VENV_BIN?=$(PYTHON_DIR)/activate
UVICORN?=$(PYTHON_DIR)/uvicorn

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
	$(CONTAINER_RT) run -p 8000:8000 -v ./:/app -it $(REPO)/$(POD):$(TAG) 
