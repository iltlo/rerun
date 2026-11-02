---
title: Viewer events in Python
hidden: true
type: feature
---

Native viewers spawned from Python can now report their events back to the script. `rerun.poll_image_click(entity_path)` returns the latest click on that image as integer pixels; `rerun.spawn` records those clicks on its own. `rerun.spawn(..., on_event=callback)` still delivers every event, including play/pause and time updates, as a typed object.

See [React to events in the Viewer](../howto/visualization/callbacks.md) and the [`rerun.spawn`](https://ref.rerun.io/docs/python/stable/common/initialization_functions/#rerun.spawn) reference.

TODO(rerun-team): add example link
