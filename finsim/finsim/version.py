"""Engine and save-format versions.

ENGINE_VERSION is the code; SAVE_VERSION is the event-log schema. A save records the SAVE_VERSION it was created
with in its WORLD_CREATED payload; `migrations.migrate` upgrades older logs on load, one step at a time.
"""
ENGINE_VERSION = "0.11.0"
SAVE_VERSION = 3
