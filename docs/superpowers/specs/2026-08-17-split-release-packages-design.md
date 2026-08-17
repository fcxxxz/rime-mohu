# Split Release Packages Design

## Goal

Replace the single combined rolling-release archive with separate Natural Code
and Flypy archives. Each archive must contain only the schemas, dictionaries,
and menu entries that can work from that archive by itself.

## Packaging Model

Keep the existing full `make dist` target for local maintenance and the current
test suite. Add parameterized split-distribution targets that assemble a common
runtime set plus one scheme-specific set:

- the Natural Code package contains `mohu_zrm*` assets;
- the Flypy package contains `mohu_flypy*` assets;
- both packages contain shared Mohu configuration, Tiger reverse lookup,
  shared Lua and OpenCC resources, documentation, and the skin editor;
- both packages contain `zh-hans-t-essay-bgw.gram`, which is used by the active
  sentence grammar configuration;
- neither package contains `zh-hans-t-essay-bgc.gram`, whose fixed-mode include
  is currently disabled.

Use an explicit common-file set and a scheme parameter instead of building the
full distribution and deleting the other scheme afterward. This makes missing
or leaked files visible in tests and avoids a growing blacklist as new assets
are added.

## Scheme Menus

Generate a package-specific `default.yaml` from the repository source while
preserving all non-schema settings and comments. Remove only schema-list rows
belonging to the other double-pinyin scheme.

The Natural Code package menu contains:

- `mohu_zrm`;
- `mohu_zrm_fixed`;
- `mohu_zrm_sentence`;
- `mohu_zrm_aux`;
- `tiger`.

The Flypy package menu contains the corresponding four `mohu_flypy` schemas
and `tiger`. Neither menu may reference a schema absent from its archive.

## Build Interface

Add Make targets that produce two independent directories, `dist-zrm` and
`dist-flypy`. The targets share the existing generated-data prerequisites so a
single `make dist-zrm dist-flypy` invocation performs source generation once.
The existing `dist` target and `DESTDIR` installation behavior remain
unchanged.

The split-package builder must reject unknown scheme names and recreate only
the requested output directory. It must not modify source configuration files.

## GitHub Actions

The build job creates and validates both split distributions. Non-pull-request
runs upload each directory as a separately named workflow artifact.

The rolling release job downloads both artifacts, creates:

- `rime-mohu-zrm-latest.zip`;
- `rime-mohu-flypy-latest.zip`;

and uploads both to the existing `latest` Release. The old
`rime-mohu-latest.zip` asset is no longer produced. When updating an existing
Release, explicitly remove the obsolete combined asset if it exists so users
cannot continue downloading a stale package.

## Validation

Add automated checks that build both distributions and assert:

- each package contains its four public schemas and their compile-only helper
  schemas and dictionaries;
- each package contains required common Lua, OpenCC, Tiger, and grammar files;
- each package excludes all files and menu rows for the other scheme;
- each package excludes `zh-hans-t-essay-bgc.gram`;
- each `default.yaml` lists exactly its four scheme schemas followed by
  `tiger`;
- the workflow creates and uploads both new ZIP names and no longer produces
  the combined ZIP.

Run the focused split-distribution checks first, then the repository's existing
configuration tests and a real two-package build. Measure the final ZIP sizes
as supporting evidence, without enforcing a brittle exact-size threshold.
