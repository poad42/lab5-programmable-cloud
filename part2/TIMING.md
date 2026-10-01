# Part 2 - instance creation and startup timings

Image: `lab5-base-image` (from snapshot `base-snapshot-lab5-flask-vm` of `lab5-flask-vm`)

Machine type: `e2-micro`, zone `us-west1-b`

| Instance | Create operation (s) | App ready, from create request (s) |
|----------|----------------------|----------------------------|
| `lab5-clone-1` | 20.2 | 64.9 |
| `lab5-clone-2` | 10.6 | 50.2 |
| `lab5-clone-3` | 13.6 | 58.2 |

Average: create 14.8 s, application ready 57.8 s

Snapshot creation: 75.7 s. Image creation: 127.3 s.
