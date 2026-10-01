#!/usr/bin/env python3
"""Part 3: create a VM that creates another VM.

This program runs on the local machine and uses an explicit service account
(service-credentials.json) to create VM-1. VM-1 reads its metadata, which
contains the service account key, a startup script for VM-2, and a Python
program that creates VM-2. VM-1 then creates VM-2, which runs the Flask
application from Part 1.

The program times how long VM-1 takes to create, and how long VM-2 takes to
become ready, measured from VM-2's creation timestamp.

The service account is created outside this program with:
  gcloud iam service-accounts create lab5-vm-creator ...
  gcloud projects add-iam-policy-binding ... --role=roles/compute.instanceAdmin.v1
  gcloud projects add-iam-policy-binding ... --role=roles/iam.serviceAccountUser
  gcloud iam service-accounts keys create service-credentials.json ...

See https://google-auth.readthedocs.io/en/latest/reference/google.oauth2.service_account.html
"""

import datetime
import os
import time
import urllib.error
import urllib.request

import googleapiclient.discovery
import google.oauth2.service_account as service_account
from googleapiclient.errors import HttpError

ZONE = "us-west1-b"
VM1_NAME = "lab5-launcher-vm"
VM2_NAME = "lab5-appliance-vm"
MACHINE_TYPE = "n2d-standard-2"
# Cheapest first. The E2 and shared-core families are out of capacity in
# us-west1-b at times, so the program falls back to an N2D/N2 machine type.
MACHINE_TYPES = [
    "f1-micro", "e2-micro", "e2-small", "e2-medium",
    "n2d-standard-2", "n2-highcpu-2", "n2-standard-2",
]
IMAGE_FAMILY = "ubuntu-2604-lts-amd64"
IMAGE_PROJECT = "ubuntu-os-cloud"
CREDENTIALS_FILE = "service-credentials.json"
APP_PORT = 5000

# The startup script that VM-1 passes to VM-2. It installs and starts Flask.
VM2_STARTUP_SCRIPT = r"""#!/bin/bash
exec > /var/log/startup-script.log 2>&1
set -x
set -e

apt-get update
apt-get install -y python3 python3-pip python3-venv git

mkdir -p /srv
cd /srv
rm -rf flask-tutorial
git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial

cd /srv/flask-tutorial
python3 -m venv /srv/venv
/srv/venv/bin/pip install --upgrade pip
/srv/venv/bin/pip install -e .

export FLASK_APP=flaskr
/srv/venv/bin/python -m flask init-db

nohup /srv/venv/bin/python -m flask run -h 0.0.0.0 --port 5000 > /var/log/flask.log 2>&1 &
"""

# The startup script for VM-1. It pulls the metadata items and runs the
# launcher program.
VM1_STARTUP_SCRIPT = r"""#!/bin/bash
exec > /var/log/startup-script.log 2>&1
set -x
set -e

apt-get update
apt-get install -y python3 python3-pip python3-venv curl

mkdir -p /srv
cd /srv
curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/attributes/vm1-launch-vm2-code -o vm1-launch-vm2.py
curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/attributes/service-credentials -o service-credentials.json
curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/attributes/vm2-startup-script -o vm2-startup-script.sh
curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/attributes/machine-type -o machine-type.txt
export GOOGLE_CLOUD_PROJECT=$(curl -s -H "Metadata-Flavor: Google" http://metadata/computeMetadata/v1/instance/attributes/project)

# A venv avoids the Debian-managed packages that pip refuses to replace.
python3 -m venv /srv/venv
/srv/venv/bin/pip install --upgrade pip
/srv/venv/bin/pip install google-api-python-client google-auth google-auth-httplib2
/srv/venv/bin/python /srv/vm1-launch-vm2.py
"""

# The program that runs on VM-1 and creates VM-2.
VM1_LAUNCH_VM2_CODE = r'''#!/usr/bin/env python3
"""Runs on VM-1: create VM-2 with the service account credentials."""
import os

import googleapiclient.discovery
import google.oauth2.service_account as service_account

ZONE = "us-west1-b"
VM2_NAME = "lab5-appliance-vm"

credentials = service_account.Credentials.from_service_account_file(
    "service-credentials.json")
project = os.environ["GOOGLE_CLOUD_PROJECT"]
compute = googleapiclient.discovery.build(
    "compute", "v1", credentials=credentials)

with open("vm2-startup-script.sh") as f:
    vm2_script = f.read()
with open("machine-type.txt") as f:
    machine_type = f.read().strip()

image = compute.images().getFromFamily(
    project="ubuntu-os-cloud", family="ubuntu-2604-lts-amd64").execute()

config = {
    "name": VM2_NAME,
    "machineType": "zones/%s/machineTypes/%s" % (ZONE, machine_type),
    "disks": [{
        "boot": True,
        "autoDelete": True,
        "initializeParams": {"sourceImage": image["selfLink"]},
    }],
    "networkInterfaces": [{
        "network": "projects/%s/global/networks/default" % project,
        "accessConfigs": [{"type": "ONE_TO_ONE_NAT", "name": "External NAT"}],
    }],
    "tags": {"items": ["allow-5000"]},
    "metadata": {"items": [{"key": "startup-script", "value": vm2_script}]},
}
compute.instances().insert(
    project=project, zone=ZONE, body=config).execute()
print("VM-1 requested VM-2:", VM2_NAME)
'''


def wait_for_operation(compute, project, operation):
    name = operation["name"]
    zone = operation.get("zone")
    while True:
        if zone:
            request = compute.zoneOperations().get(
                project=project, zone=zone.split("/")[-1], operation=name)
        else:
            request = compute.globalOperations().get(
                project=project, operation=name)
        result = request.execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise RuntimeError(result["error"])
            return result
        time.sleep(1)


def resource_exists(get_request):
    try:
        get_request().execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def capacity_error(error):
    """True when the zone cannot supply the machine type right now."""
    text = str(error)
    return ("resource_availability" in text or "stockout" in text or
            "ZONE_RESOURCE_POOL_EXHAUSTED" in text)


def machine_types_for_source(compute, project):
    """Try the Part 1 VM's type first, then the other cheap types."""
    types = []
    try:
        instance = compute.instances().get(
            project=project, zone=ZONE, instance="lab5-flask-vm").execute()
        types.append(instance["machineType"].split("/")[-1])
    except HttpError:
        pass
    for machine_type in MACHINE_TYPES:
        if machine_type not in types:
            types.append(machine_type)
    return types


def create_vm1(compute, project, machine_type):
    image = compute.images().getFromFamily(
        project=IMAGE_PROJECT, family=IMAGE_FAMILY).execute()
    with open(CREDENTIALS_FILE) as f:
        service_credentials = f.read()
    config = {
        "name": VM1_NAME,
        "machineType": "zones/%s/machineTypes/%s" % (ZONE, machine_type),
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {"sourceImage": image["selfLink"]},
        }],
        "networkInterfaces": [{
            "network": "projects/%s/global/networks/default" % project,
            "accessConfigs": [{"type": "ONE_TO_ONE_NAT",
                               "name": "External NAT"}],
        }],
        "metadata": {
            "items": [
                {"key": "startup-script", "value": VM1_STARTUP_SCRIPT},
                {"key": "vm1-launch-vm2-code",
                 "value": VM1_LAUNCH_VM2_CODE},
                {"key": "vm2-startup-script",
                 "value": VM2_STARTUP_SCRIPT},
                {"key": "service-credentials",
                 "value": service_credentials},
                {"key": "machine-type", "value": machine_type},
                {"key": "project", "value": project},
            ],
        },
    }
    operation = compute.instances().insert(
        project=project, zone=ZONE, body=config).execute()
    return wait_for_operation(compute, project, operation)


def get_instance(compute, project, name):
    return compute.instances().get(
        project=project, zone=ZONE, instance=name).execute()


def get_external_ip(compute, project, name):
    return get_instance(compute, project, name)[
        "networkInterfaces"][0]["accessConfigs"][0]["natIP"]


def wait_for_app(ip, port=APP_PORT, timeout=900, interval=2):
    """Poll the app until /hello answers, returning the seconds it took."""
    start = time.time()
    url = "http://%s:%d/hello" % (ip, port)
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return time.time() - start
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval)
    return None


def main():
    with open(CREDENTIALS_FILE) as f:
        credentials = service_account.Credentials.from_service_account_file(
            CREDENTIALS_FILE)
    project = (os.getenv("GOOGLE_CLOUD_PROJECT")
               or credentials.project_id)
    compute = googleapiclient.discovery.build(
        "compute", "v1", credentials=credentials)

    print("Project: %s" % project)
    print("Service account: %s" % credentials.service_account_email)

    machine_types = machine_types_for_source(compute, project)
    print("Machine type candidates: %s" % ", ".join(machine_types))

    vm1_create_seconds = 0.0
    machine_type = machine_types[0]
    if resource_exists(lambda: compute.instances().get(
            project=project, zone=ZONE, instance=VM1_NAME)):
        print("VM-1 %s already exists." % VM1_NAME)
    else:
        for machine_type in machine_types:
            try:
                print("Creating VM-1 %s as %s (this VM creates VM-2)..." %
                      (VM1_NAME, machine_type))
                start = time.time()
                create_vm1(compute, project, machine_type)
                vm1_create_seconds = time.time() - start
                print("VM-1 created as %s in %.1f s." %
                      (machine_type, vm1_create_seconds))
                break
            except RuntimeError as e:
                if machine_type != machine_types[-1] and capacity_error(e):
                    print("%s is out of capacity in %s" %
                          (machine_type, ZONE))
                    continue
                raise

    print("Waiting for VM-1 to create VM-2...")
    vm2 = None
    for _ in range(120):
        try:
            candidate = get_instance(compute, project, VM2_NAME)
            access_config = candidate["networkInterfaces"][0]["accessConfigs"][0]
            if access_config.get("natIP"):
                vm2 = candidate
                break
        except HttpError as e:
            if e.resp.status != 404:
                raise
        time.sleep(5)
    if vm2 is None:
        print("VM-2 has not appeared with an external IP yet; check "
              "/var/log/startup-script.log on %s." % VM1_NAME)
        return

    created = datetime.datetime.fromisoformat(vm2["creationTimestamp"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    ip = vm2["networkInterfaces"][0]["accessConfigs"][0]["natIP"]
    print("VM-1 created VM-2 at %s." % vm2["creationTimestamp"])

    startup_seconds = wait_for_app(ip)
    if startup_seconds is None:
        print("VM-2 did not answer within the timeout.")
        return
    ready_at = datetime.datetime.now(datetime.timezone.utc)
    vm2_total_seconds = (ready_at - created).total_seconds()

    print()
    print("VM-1 create operation: %.1f s" % vm1_create_seconds)
    print("VM-2 application ready: %.1f s after VM-2's creation "
          "timestamp" % vm2_total_seconds)
    print()
    print("The Flask application is available at:")
    print()
    print("http://%s:%d" % (ip, APP_PORT))


if __name__ == "__main__":
    main()