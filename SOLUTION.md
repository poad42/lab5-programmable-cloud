# Lab 5 - Programmable Cloud: Solution

## What this does

Three programs use the Compute Engine API to build and clone cloud resources:

1. Part 1 creates a VM and installs the Flask tutorial application on it.
2. Part 2 snapshots that VM, turns the snapshot into an image, and creates three clones from the image, timing each one.
3. Part 3 creates a VM that uses an explicit service account to create a second VM running the Flask application.

All runs use the project `ardent-quarter-510218-a6`, zone `us-west1-b`, and the `ubuntu-2604-lts-amd64` image family.

## Part 1 - create a VM and install the application

`part1/part1.py`:

- checks for the `allow-5000` firewall rule and creates it when missing (TCP port 5000 from `0.0.0.0/0`, target tag `allow-5000`),
- creates `lab5-flask-vm` with an `ONE_TO_ONE_NAT` external IP and a startup script,
- applies the `allow-5000` network tag with `instances.setTags`,
- reads the external IP, prints the URL, and times how long the application takes to answer.

The startup script installs `python3`, `python3-pip`, `python3-venv`, and `git`, clones the flask-tutorial repository, installs it into a venv, runs `flask init-db`, and starts `flask run -h 0.0.0.0 --port 5000`.

Result: `http://8.229.169.154:5000/hello` returns `Hello, World!`.

### Machine type and capacity

The lab suggests `f1-micro`. Capacity for the cheap families in `us-west1-b` comes and goes during the run: at different points `f1-micro`, `e2-micro`, `e2-small`, and `e2-medium` all returned `ZONE_RESOURCE_POOL_EXHAUSTED` or `stockout`. The program tries those cheapest types first and falls back to `n2d-standard-2`. The Part 1 run used `e2-micro` (2 shared vCPU, 1 GB). The Part 3 run fell through to `n2d-standard-2` (2 vCPU, 8 GB).

### Part 1 timings

| Measurement | Time |
|-------------|------|
| Instance create operation | 23.2 s |
| Application ready, from the create request | 416.9 s |
| Application ready, after the create operation finished | 393.7 s |

The 394 s is the time for the startup script to run apt, clone the repository, build the venv, install Flask, and start it, on a shared-core `e2-micro`. On the dedicated `n2d-standard-2` that Part 3 used, the same install took 73.3 s, so the machine type dominates this number.

## Part 2 - clone a machine

`part2/part2.py`:

- stops `lab5-flask-vm` so its disk is quiescent,
- creates the snapshot `base-snapshot-lab5-flask-vm` from the boot disk,
- starts the instance again,
- creates the image `lab5-base-image` from the snapshot,
- creates `lab5-clone-1`, `lab5-clone-2`, and `lab5-clone-3` from the image, timing the create operation and the time until each clone serves the application.

| Step | Time |
|------|------|
| Snapshot creation | 75.7 s |
| Image creation | 127.3 s |

| Instance | Create operation (s) | App ready, from create request (s) |
|----------|----------------------|----------------------------|
| `lab5-clone-1` | 20.2 | 64.9 |
| `lab5-clone-2` | 10.6 | 50.2 |
| `lab5-clone-3` | 13.6 | 58.2 |
| Average | 14.8 | 57.8 |

The clones are `e2-micro` and boot from the image with Flask already installed, so their startup script only launches the application. The 50 to 65 s is mostly VM boot plus the application launch, not installation. All three clones serve `Hello, World!` on port 5000.

The same numbers are recorded in `part2/TIMING.md`.

### Why the instance is stopped first

The first attempt snapshotted the running instance. That snapshot was crash-consistent but not clean: the clones came up with a zero-byte `/srv/venv/bin/flask` and missing package metadata, so the application would not start. Stopping the instance first makes the filesystem quiescent, and the image is then a faithful copy.

## Part 3 - create a VM from a VM

The service account `lab5-vm-creator@ardent-quarter-510218-a6.iam.gserviceaccount.com` was created with `roles/compute.instanceAdmin.v1` and `roles/iam.serviceAccountUser`, and a JSON key was written to `service-credentials.json` (gitignored).

`part3/part3.py` runs locally and uses that key to create VM-1 (`lab5-launcher-vm`). VM-1 receives the key, a Flask startup script for VM-2, a launcher program, the machine type, and the project id as instance metadata. VM-1's startup script:

- installs `python3`, `python3-pip`, `python3-venv`, and `curl`,
- downloads the metadata items with `curl` and the `Metadata-Flavor: Google` header,
- installs the Google API client into a venv,
- runs the launcher, which creates VM-2 (`lab5-appliance-vm`) with the Flask startup script.

| Measurement | Time |
|-------------|------|
| VM-1 create operation | 9.7 s |
| VM-2 application ready, from VM-2's creation timestamp | 73.3 s |

VM-1's startup log ends with `VM-1 requested VM-2: lab5-appliance-vm`. VM-2 serves the application at `http://34.82.215.132:5000/hello`.

Both VMs ran as `n2d-standard-2` for this run, which is why VM-2's install took 73.3 s rather than the 394 s the shared-core Part 1 VM needed.

### Why VM-1 uses a venv

The first VM-1 startup script ran `pip3 install --break-system-packages google-api-python-client`. That failed because pip tried to replace the Debian-managed `requests` package (`uninstall-no-record-file`). A venv avoids the conflict.

## Application startup summary

| Part | Machine type | What the startup script does | App ready |
|------|--------------|------------------------------|-----------|
| 1 | `e2-micro` | installs Flask from scratch | 416.9 s from the create request |
| 2 | `e2-micro` | launches the pre-installed app | 50.2 to 64.9 s from the create request |
| 3 | `n2d-standard-2` | installs Flask from scratch | 73.3 s from VM-2's creation timestamp |

The install steps dominate. Running apt, a git clone, a venv build, and a pip install costs about 400 s on the 2-shared-vCPU, 1 GB `e2-micro`, and about 73 s on the dedicated `n2d-standard-2`. Booting from an image that already contains the application costs about a minute on either type.

## Software needed

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.14 | Ubuntu 26.04 container and VMs |
| google-api-python-client | 2.200.0 | Compute Engine client |
| google-auth | 2.59.1 | Application Default Credentials and service account keys |
| gcloud SDK | 583.0.0 | host side: project setup, service account, cleanup |
| Docker + Docker Compose | current | runs the container |
| Flask | 3.1.3 | installed on the VMs by the startup scripts |

The container is a Docker image on Ubuntu 26.04 (`Dockerfile`, `docker-compose.yml`). It mounts the host's Application Default Credentials read only and sets `GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_CLOUD_PROJECT`.

The programs need `gcloud auth application-default login` on the host for Parts 1 and 2, and the service account key for Part 3.

## How to run

```bash
docker compose up --build -d
docker exec -it lab5-cloud bash

cd /lab/part1 && python3 part1.py
cd ../part2 && python3 part2.py
cd ../part3 && python3 part3.py
```

Part 3 needs the service account key, created once with:

```bash
gcloud iam service-accounts create lab5-vm-creator \
  --display-name="Lab 5 VM creator" --project=ardent-quarter-510218-a6
gcloud projects add-iam-policy-binding ardent-quarter-510218-a6 \
  --member=serviceAccount:lab5-vm-creator@ardent-quarter-510218-a6.iam.gserviceaccount.com \
  --role=roles/compute.instanceAdmin.v1
gcloud projects add-iam-policy-binding ardent-quarter-510218-a6 \
  --member=serviceAccount:lab5-vm-creator@ardent-quarter-510218-a6.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountUser
gcloud iam service-accounts keys create service-credentials.json \
  --iam-account=lab5-vm-creator@ardent-quarter-510218-a6.iam.gserviceaccount.com
```

## Results

| Part | Resource | Result |
|------|----------|--------|
| 1 | `lab5-flask-vm` | create 23.2 s, app ready 416.9 s, `/hello` returns `Hello, World!` |
| 2 | `base-snapshot-lab5-flask-vm`, `lab5-base-image`, three clones | snapshot 75.7 s, image 127.3 s, clones create 14.8 s average, app ready 57.8 s average |
| 3 | `lab5-launcher-vm` creates `lab5-appliance-vm` | VM-1 create 9.7 s, VM-2 app ready 73.3 s, `/hello` returns `Hello, World!` |

## Notes and caveats

- Capacity for the cheap families in `us-west1-b` fluctuates, so every program tries the cheapest machine type first and falls back to the next one. The runs used `e2-micro` for Parts 1 and 2 and `n2d-standard-2` for Part 3.
- The application startup time depends mostly on whether the startup script installs Flask or only launches it, and on the machine type when it installs. The shared-core `e2-micro` was about five times slower at the install than the dedicated `n2d-standard-2`.
- The service account key is passed to VM-1 through instance metadata, which any principal with project read access can see. The lab notes this tradeoff. The key is gitignored, and it was deleted with the service account after the runs.
- Ubuntu 26.04 ships Python 3.14 and marks the system Python as externally managed, so the startup scripts install into venvs.
- The flask-tutorial repository pins no Flask version, so pip installs a current Flask (3.1.3) that runs on Python 3.14.

## Resources and collaborators

* Author: Adhitya Mohan (CSCI 4253/5253)
* Collaborators: none
* GCP resources used: project `ardent-quarter-510218-a6`, zone `us-west1-b`, six VMs, the firewall rule `allow-5000`, the snapshot `base-snapshot-lab5-flask-vm`, the image `lab5-base-image`, and the service account `lab5-vm-creator`. All of these were deleted after the runs.
* External resources used:
  * Google Cloud Python tutorial (https://cloud.google.com/compute/docs/tutorials/python-guide)
  * Compute Engine REST API reference (https://cloud.google.com/compute/docs/reference/rest/v1/)
  * Flask tutorial application (https://github.com/cu-csci-4253-datacenter/flask-tutorial)
  * google-auth service account documentation (https://google-auth.readthedocs.io/)