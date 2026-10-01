#!/usr/bin/env python3
"""Part 2: clone the Part 1 machine with a snapshot and an image.

The program:
  * creates a snapshot of the Part 1 instance's boot disk
    (named base-snapshot-<instance>),
  * creates a custom VM image from that snapshot,
  * creates three instances from the image and times each creation,
  * times how long each clone takes to serve the application,
  * writes the timings to TIMING.md.

Adapted from the Google Cloud Compute Engine API samples.
"""

import time
import urllib.error
import urllib.request

import googleapiclient.discovery
import google.auth
from googleapiclient.errors import HttpError

ZONE = "us-west1-b"
SOURCE_INSTANCE = "lab5-flask-vm"
SNAPSHOT_NAME = "base-snapshot-%s" % SOURCE_INSTANCE
IMAGE_NAME = "lab5-base-image"
CLONE_PREFIX = "lab5-clone"
CLONE_COUNT = 3
APP_PORT = 5000
TIMING_FILE = "TIMING.md"


def wait_for_operation(compute, project, operation):
    """Poll an operation until it finishes.

    Reads the operation dict and picks the matching zone, region, or global
    endpoint, since snapshots, images, and instances report differently.
    """
    name = operation["name"]
    zone = operation.get("zone")
    region = operation.get("region")
    while True:
        if zone:
            request = compute.zoneOperations().get(
                project=project, zone=zone.split("/")[-1], operation=name)
        elif region:
            request = compute.regionOperations().get(
                project=project, region=region.split("/")[-1], operation=name)
        else:
            request = compute.globalOperations().get(
                project=project, operation=name)
        result = request.execute()
        if result["status"] == "DONE":
            if "error" in result:
                raise RuntimeError(result["error"])
            return result
        time.sleep(1)


def get_instance(compute, project, zone, name):
    return compute.instances().get(
        project=project, zone=zone, instance=name).execute()


def get_boot_disk(compute, project, zone, instance_name):
    instance = get_instance(compute, project, zone, instance_name)
    for disk in instance["disks"]:
        if disk.get("boot"):
            return disk["source"].split("/")[-1]
    raise RuntimeError("no boot disk found on %s" % instance_name)


def get_external_ip(compute, project, zone, name):
    instance = get_instance(compute, project, zone, name)
    return instance["networkInterfaces"][0]["accessConfigs"][0]["natIP"]


def resource_exists(get_request):
    try:
        get_request().execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def create_snapshot(compute, project, zone, disk, name):
    """Create a snapshot of the disk with disks.createSnapshot."""
    operation = compute.disks().createSnapshot(
        project=project, zone=zone, disk=disk,
        body={"name": name,
              "description": "Base snapshot of %s" % SOURCE_INSTANCE},
    ).execute()
    return wait_for_operation(compute, project, operation)


def stop_instance(compute, project, zone, name):
    """Stop the instance so the disk is quiescent and the snapshot is clean."""
    operation = compute.instances().stop(
        project=project, zone=zone, instance=name).execute()
    return wait_for_operation(compute, project, operation)


def start_instance(compute, project, zone, name):
    operation = compute.instances().start(
        project=project, zone=zone, instance=name).execute()
    return wait_for_operation(compute, project, operation)


def create_image(compute, project, name, snapshot):
    """Create a global image from the snapshot with images.insert."""
    body = {
        "name": name,
        "sourceSnapshot": "projects/%s/global/snapshots/%s" % (project, snapshot),
        "description": "Image built from %s" % snapshot,
    }
    operation = compute.images().insert(project=project, body=body).execute()
    return wait_for_operation(compute, project, operation)


def create_instance_from_image(compute, project, zone, name, image,
                               machine_type):
    """Create a VM whose boot disk is initialized from the image."""
    config = {
        "name": name,
        "machineType": "zones/%s/machineTypes/%s" % (zone, machine_type),
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {"sourceImage": image["selfLink"]},
        }],
        "networkInterfaces": [{
            "network": "projects/%s/global/networks/default" % project,
            "accessConfigs": [{
                "type": "ONE_TO_ONE_NAT",
                "name": "External NAT",
            }],
        }],
        # The app is already installed in the image; this just starts it.
        "tags": {"items": ["allow-5000"]},
        "metadata": {
            "items": [{
                "key": "startup-script",
                "value": (
                    "#!/bin/bash\n"
                    "cd /srv/flask-tutorial\n"
                    "export FLASK_APP=flaskr\n"
                    "nohup /srv/venv/bin/python -m flask run -h 0.0.0.0 "
                    "--port 5000 > /var/log/flask.log 2>&1 &\n"
                ),
            }],
        },
    }
    operation = compute.instances().insert(
        project=project, zone=zone, body=config).execute()
    return wait_for_operation(compute, project, operation)


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
    credentials, project = google.auth.default()
    compute = googleapiclient.discovery.build(
        "compute", "v1", credentials=credentials)

    print("Project: %s" % project)
    disk = get_boot_disk(compute, project, ZONE, SOURCE_INSTANCE)
    machine_type = get_instance(
        compute, project, ZONE, SOURCE_INSTANCE)["machineType"].split("/")[-1]
    print("Source instance: %s (disk %s, %s)" %
          (SOURCE_INSTANCE, disk, machine_type))

    snapshot_seconds = 0.0
    if resource_exists(lambda: compute.snapshots().get(
            project=project, snapshot=SNAPSHOT_NAME)):
        print("Snapshot %s already exists." % SNAPSHOT_NAME)
    else:
        print("Stopping %s for a consistent snapshot..." % SOURCE_INSTANCE)
        stop_instance(compute, project, ZONE, SOURCE_INSTANCE)
        print("Creating snapshot %s..." % SNAPSHOT_NAME)
        start = time.time()
        create_snapshot(compute, project, ZONE, disk, SNAPSHOT_NAME)
        snapshot_seconds = time.time() - start
        print("Snapshot created in %.1f s. Starting %s again..." %
              (snapshot_seconds, SOURCE_INSTANCE))
        start_instance(compute, project, ZONE, SOURCE_INSTANCE)

    image_seconds = 0.0
    if resource_exists(lambda: compute.images().get(
            project=project, image=IMAGE_NAME)):
        print("Image %s already exists." % IMAGE_NAME)
    else:
        print("Creating image %s from %s..." % (IMAGE_NAME, SNAPSHOT_NAME))
        start = time.time()
        create_image(compute, project, IMAGE_NAME, SNAPSHOT_NAME)
        image_seconds = time.time() - start
        print("Image created in %.1f s." % image_seconds)

    image = compute.images().get(
        project=project, image=IMAGE_NAME).execute()

    timings = []
    for i in range(1, CLONE_COUNT + 1):
        name = "%s-%d" % (CLONE_PREFIX, i)
        if resource_exists(lambda: compute.instances().get(
                project=project, zone=ZONE, instance=name)):
            print("%s already exists, skipping." % name)
            continue
        start = time.time()
        create_instance_from_image(compute, project, ZONE, name, image,
                                   machine_type)
        create_seconds = time.time() - start
        ip = get_external_ip(compute, project, ZONE, name)
        startup_seconds = wait_for_app(ip)
        total_seconds = time.time() - start
        timings.append((name, create_seconds, startup_seconds, total_seconds))
        print("Created %s in %.1f s; application ready %.1f s after the "
              "create request" % (name, create_seconds, total_seconds))

    with open(TIMING_FILE, "w") as f:
        f.write("# Part 2 - instance creation and startup timings\n\n")
        f.write("Image: `%s` (from snapshot `%s` of `%s`)\n\n" %
                (IMAGE_NAME, SNAPSHOT_NAME, SOURCE_INSTANCE))
        f.write("Machine type: `%s`, zone `%s`\n\n" % (machine_type, ZONE))
        f.write("| Instance | Create operation (s) | App ready, from create "
                "request (s) |\n")
        f.write("|----------|----------------------|----------------------------|\n")
        for name, create_seconds, startup_seconds, total_seconds in timings:
            f.write("| `%s` | %.1f | %.1f |\n" %
                    (name, create_seconds, total_seconds))
        if timings:
            create_avg = sum(t[1] for t in timings) / len(timings)
            total_avg = sum(t[3] for t in timings) / len(timings)
            f.write("\nAverage: create %.1f s, application ready %.1f s\n" %
                    (create_avg, total_avg))
        f.write("\nSnapshot creation: %.1f s. Image creation: %.1f s.\n" %
                (snapshot_seconds, image_seconds))
    print("Wrote %s" % TIMING_FILE)


if __name__ == "__main__":
    main()