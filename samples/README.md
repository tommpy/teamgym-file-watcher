# samples/ — EXIF test fixtures

Real JPEGs written by Lightroom Classic (15.0.1, Windows), used as test fixtures for EXIF
capture-time extraction (build step 07) and integration tests (step 10).

History: these were originally collected to reverse engineer the Lightroom plugin's XMP
format. Analysis showed Lightroom **never writes plugin custom metadata to files** —
`setPropertyForPlugin` data is catalog-only — so no file metadata is written by the watcher
at all (see PLAN.md, "Scope decision"). The files remain valuable as realistic EXIF/XMP
inputs: three contain standard Lightroom XMP packets, two contain EXIF only.

The neighbouring `.json` files describe the tag each photo corresponds to in the sorter API
(display values as shown in Lightroom's metadata panel). Useful when constructing matching
scenarios in tests. `photo_1784567438078.json` is intentionally empty (untagged control).

Note for test authors: `Uppsala_Athletics_TUMBLE_…json` contains `"Round Type": "Mini"`,
which is not in the plugin's enum (FULL/MICRO) — treat API values defensively.
