# RaG Economy Manager 0.81 Beta

## Release Type

Public beta. Use copied mission files first.

## Main Additions

- Added dedicated Environment category for explained `cfgenvironment.xml` editing.
- Added Event System editor for `cfgeventspawns.xml` and `cfgeventgroups.xml`.
- Added event creation with filtered classname search.
- Added spawn-event positions, dynamic zones, grouped layouts, layout children, and reference-aware rename/delete.
- Added map batch placement for event spawns with selectable markers, drag movement, rotation, north-facing defaults, and X/Z/Y precision capped at six decimals.
- Added territory icon selection using bundled icons and expanded animal/creature choices.
- Added event-to-environment registration when creating relevant territories.
- Added mission-wide `storage_1` exclusion from discovery, loading, validation, backup, restore, and save.

## Validation And Save Changes

- `mapgrouppos.xml` placements without matching `mapgroupproto.xml` prototypes now warn.
- Reusable prototypes without placed instances do not warn.
- Group lootmax below container lootmax totals does not warn.
- Mapgroupproto saves preserve validation results and remove resolved entries without forcing full validation.
- Commented Mapgroupproto groups, containers, points, and XML preview content remain visible and can be reactivated.
- Event, spawn, group, secondary, environment, and territory references are cross-checked.
- Event-spawn and event-group writers preserve comments, unknown XML content, and optional attributes.

## Current Module Coverage

- Dashboard
- Types
- Spawnabletypes
- Randompresets
- Definitions
- Economy Core
- Events and Event System
- Environment
- Territories
- CE Zones
- Mapgroupproto
- Weather
- Configs
- Profiles
- Logs Analyzer
- Tools

## Build And Update

- Windows `onedir` build through PyInstaller.
- Per-user installer through Inno Setup.
- GitHub Releases publisher with tests, installer build, SHA-256 checksum, tag, and prerelease handling.
- In-app updater verifies checksums and launches downloaded installers only after unsaved changes are resolved.

## Known Limitations

- Beta release. Test with copied mission files.
- Validation cannot guarantee every DayZ Central Economy runtime behavior.
- Minidump analysis remains limited without private Bohemia DayZ symbols.
- C/CFG files are highlighted but not fully compiled or interpreted.
- Map editing requires a matching map image and correct world-size metadata.
- Full undo/redo is not yet available in every module.

## Recommended Tester Flow

1. Copy mission folder.
2. Open copied mission.
3. Choose, download, or import map.
4. Run `Validate Workspace`.
5. Test Events, Event System, Environment, and Territories together.
6. Save selected sources and reopen mission.
7. Generate Validation Report.
8. Test `Save All` only after checking dirty sources.
