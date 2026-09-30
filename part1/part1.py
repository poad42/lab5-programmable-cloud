#!/usr/bin/env python3
"""Part 1: create a VM that runs the Flask tutorial application.

The program uses the Compute Engine API to:
  * create a VM in us-west1-b,
  * pass a startup script that installs and starts the Flask app,
  * create the allow-5000 firewall rule if it does not exist,
  * tag the VM so the firewall rule applies to it,
  * print the URL of the running application.

Adapted from the Google Cloud "create_instance.py" sample:
https://github.com/GoogleCloudPlatform/python-docs-samples/blob/main/compute/api/create_instance.py
"""

import time

import googleapiclient.discovery
import google.auth
from googleapiclient.errors import HttpError

PROJECT = None
ZONE = "us-west1-b"
INSTANCE_NAME = "lab5-flask-vm"
# Cheapest first. The E2 and shared-core families are out of capacity in
# us-west1-b at times, so the program falls back to an N2D/N2 machine type.
MACHINE_TYPES = [
    "f1-micro", "e2-micro", "e2-small", "e2-medium",
    "n2d-standard-2", "n2-highcpu-2", "n2-standard-2",
]
IMAGE_FAMILY = "ubuntu-2604-lts-amd64"
IMAGE_PROJECT = "ubuntu-os-cloud"
NETWORK = "default"
TAG = "allow-5000"
FIREWALL_RULE = "allow-5000"
APP_PORT = 5000

# Installs the Flask tutorial application and starts it on port 5000.
# The output is written to /var/log/startup-script.log for debugging.
STARTUP_SCRIPT = r"""#!/bin/bash
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
/srv/venv/bin/flask init-db

nohup /srv/venv/bin/flask run -h 0.0.0.0 --port 5000 > /var/log/flask.log 2>&1 &
"""


def wait_for_operation(compute, project, zone, operation):
    """Poll a zone operation until it finishes."""
    print("Waiting for operation to finish...")
    while True:
        result = compute.zoneOperations().get(
            project=project, zone=zone, operation=operation).execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise RuntimeError(result["error"])
            return result
        time.sleep(1)


def wait_for_global_operation(compute, project, operation):
    """Poll a global operation until it finishes (firewalls, images, snapshots)."""
    print("Waiting for global operation to finish...")
    while True:
        result = compute.globalOperations().get(
            project=project, operation=operation).execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise RuntimeError(result["error"])
            return result
        time.sleep(1)


def get_image_from_family(compute, project, family):
    """Return the newest image in the given image family."""
    image = compute.images().getFromFamily(
        project=project, family=family).execute()
    return image


def instance_exists(compute, project, zone, name):
    try:
        compute.instances().get(
            project=project, zone=zone, instance=name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def create_instance(compute, project, zone, name, machine_type, image):
    """Create a VM with an external IP and the Flask startup script."""
    config = {
        "name": name,
        "machineType": "zones/%s/machineTypes/%s" % (zone, machine_type),
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {"sourceImage": image["selfLink"]},
        }],
        "networkInterfaces": [{
            "network": "projects/%s/global/networks/%s" % (project, NETWORK),
            # ONE_TO_ONE_NAT gives the VM an external IP address.
            "accessConfigs": [{
                "type": "ONE_TO_ONE_NAT",
                "name": "External NAT",
            }],
        }],
        "metadata": {
            "items": [{
                "key": "startup-script",
                "value": STARTUP_SCRIPT,
            }],
        },
    }
    operation = compute.instances().insert(
        project=project, zone=zone, body=config).execute()
    return wait_for_operation(compute, project, zone, operation["name"])


def firewall_exists(compute, project, name):
    try:
        compute.firewalls().get(project=project, firewall=name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def create_firewall(compute, project):
    """Allow TCP port 5000 from anywhere to instances tagged allow-5000."""
    body = {
        "name": FIREWALL_RULE,
        "network": "projects/%s/global/networks/%s" % (project, NETWORK),
        "allowed": [{"IPProtocol": "tcp", "ports": [str(APP_PORT)]}],
        "sourceRanges": ["0.0.0.0/0"],
        "targetTags": [TAG],
        "description": "Allow the Flask app on port 5000",
    }
    operation = compute.firewalls().insert(
        project=project, body=body).execute()
    return wait_for_global_operation(compute, project, operation["name"])


def set_tags(compute, project, zone, name, tags):
    """Apply network tags with instances.setTags."""
    instance = compute.instances().get(
        project=project, zone=zone, instance=name).execute()
    body = {
        "items": tags,
        "fingerprint": instance["tags"]["fingerprint"],
    }
    operation = compute.instances().setTags(
        project=project, zone=zone, instance=name, body=body).execute()
    return wait_for_operation(compute, project, zone, operation["name"])


def get_external_ip(compute, project, zone, name):
    instance = compute.instances().get(
        project=project, zone=zone, instance=name).execute()
    return instance["networkInterfaces"][0]["accessConfigs"][0]["natIP"]


def main():
    credentials, project = google.auth.default()
    compute = googleapiclient.discovery.build(
        "compute", "v1", credentials=credentials)

    print("Project: %s" % project)
    print("Zone: %s" % ZONE)

    if not firewall_exists(compute, project, FIREWALL_RULE):
        print("Creating firewall rule %s..." % FIREWALL_RULE)
        create_firewall(compute, project)
    else:
        print("Firewall rule %s already exists." % FIREWALL_RULE)

    if not instance_exists(compute, project, ZONE, INSTANCE_NAME):
        image = get_image_from_family(compute, IMAGE_PROJECT, IMAGE_FAMILY)
        for machine_type in MACHINE_TYPES:
            try:
                print("Creating instance %s (%s) from %s..." %
                      (INSTANCE_NAME, machine_type, image["name"]))
                create_instance(compute, project, ZONE, INSTANCE_NAME,
                                machine_type, image)
                print("Created %s as %s" % (INSTANCE_NAME, machine_type))
                break
            except RuntimeError as e:
                if machine_type != MACHINE_TYPES[-1] and \
                        "resource_availability" in str(e):
                    print("%s is out of capacity in %s" % (machine_type, ZONE))
                    continue
                raise
    else:
        print("Instance %s already exists." % INSTANCE_NAME)

    print("Applying network tag %s..." % TAG)
    set_tags(compute, project, ZONE, INSTANCE_NAME, [TAG])

    ip = get_external_ip(compute, project, ZONE, INSTANCE_NAME)
    print()
    print("The Flask application is available at:")
    print()
    print("http://%s:%d" % (ip, APP_PORT))
    print()
    print("The startup script takes a minute or two to install Flask.")


if __name__ == "__main__":
    main()