# RaG Economy Manager TODO

## Current Status

0.81 Beta is publishable as a tester beta. Core app builds as a Windows `onedir` application plus Inno Setup installer and has source-aware save safety with rolling backups.

## Completed / No Longer Roadmap

- Dashboard with mission overview, analysis report, dirty source display, and issue navigation targets.
- Getting Started walkthrough with `Don't show again` checkbox and top-bar Guide button.
- Mission-wide Validation Report export as TXT and JSON.
- Types module with source-aware editing, full XML editing, relation controls, flags, filters, duplicate resolver, type splitting, linked Spawnabletypes/Randompresets actions, and validation tuning.
- Spawnabletypes module with mission discovery, add/delete entries, damage/hoarder editing, cargo/attachment blocks, item editing, preset dropdowns from Randompresets, references, XML preview, save/change reports.
- Randompresets module with mission discovery, cargo/attachment presets, add/delete/edit items, item chance editing, usage lookup from Spawnabletypes, XML preview, save/change reports.
- Definitions module for category, usage, value, and tag names from `cfglimitsdefinition.xml`.
- Economy Core module for `cfgeconomycore.xml` CE managed classes, server defaults, extra economy files, XML preview highlighting, registered-folder dropdowns, and practical root-class explanations.
- Events module with direct editing, child events, relationship analysis, spawn/territory location viewing, map plotting, and add-event classname filtering.
- Event System editor for `cfgeventspawns.xml` and `cfgeventgroups.xml`, including source-safe CRUD, zones, grouped layouts, reference-aware rename/delete, map batch placement, drag movement, rotation, and six-decimal coordinate normalization.
- Environment module for explained `cfgenvironment.xml` file registrations, territory mappings, populations, agents, spawn classnames, runtime settings, and event-to-environment registration.
- Territories module with env loading, grouped layers, map-first editor, add/duplicate/delete zones, move/resize, multi-select, box-select, AI Usage editing, custom territory sources, undo/redo, related event navigation.
- CE Zones module with CE Tool project XML parsing, RLE grayscale TGA layer decoding, grouped layer viewer, map overlay rendering, usage/value painting, layer save/export/import, position inspection, and matching Types filter.
- CE Zones map controls with zoom, pan, map source comparison, true alpha opacity, paint undo/redo, brush slider, dirty layer badges, and `areaflags.map` import/export.
- Mapgroupproto module with dedicated navigation outside Configs, mission-root/direct-file detection, grouped prototype/container/point inspection, placement counts from `mapgrouppos.xml`, asymmetric missing-prototype validation, loaded Types relation matching, structural validation, commented-entry visibility/reactivation, selected-group XML preview, search/filter, exact issue jumps, source-safe save, add group/container, guided group/container/point editing, and selected group/container/point comment-out cleanup.
- Weather module with `cfgweather.xml` editing, validation, official default, custom default file, seasonal presets, custom presets, and delete custom preset.
- Configs module with mission config grouping, XML/JSON/C/CFG highlighting, line numbers, validation, and save safety.
- Profiles module with optional external profiles folder, tree display, syntax highlighting, line numbers, validation, and save safety.
- Logs Analyzer module with folder/file session tree, selected-file scans, readable crash/log summaries, and optional deep minidump analysis.
- Map assets from GitHub cache plus local custom map import.
- Animal/infected map icons for territory/event map markers.
- Rolling backup restore tool.
- Validation Issue Navigation Polish: dashboard issue jumps now return clear success/fail state, route CE Zones layer issues, include validation report targets, and block module-owned files from unsafe Configs saves.
- Validation Quick Fixes started: missing `cfgspawnabletypes.xml` and `cfgrandompresets.xml` can be created safely under mission `db\` when user confirms.
- Validation Quick Fixes: unlinked split `types`, `spawnabletypes`, and `randompresets` XML files can be linked in `cfgeconomycore.xml` from registered CE folders only, with a selection window, confirmation by action, and backup.
- Tools can create a fresh mission from BohemiaInteractive/DayZ-Central-Economy official GitHub templates.
- Loot Distribution / Rarity report with item-first table, relation capacity summary, mapgroup opportunity matching, spawnabletypes/randompresets derived availability, event child availability, hoarding sensitivity, rarity labels, and CSV/JSON export.
- README and release notes through 0.81 Beta.
- Windows Inno Setup installer pipeline with per-user installation, shortcuts, uninstall support, and SHA-256 release checksum output.
- Manual GitHub Releases updater with prerelease support, version comparison, verified installer download, and unsaved-change protection.
- PBO Builder-style About and Licence windows plus installer licence display.
- Mission-wide hard exclusion for `storage_1` during discovery, loading, validation, backup, restore, and save.

## Priority Roadmap

1. Validation Quick Fixes
   - Create missing usage, value, category, and tag definitions in `cfglimitsdefinition.xml`.
   - Remove dead references only when target is clearly invalid and user confirms.
   - Move/copy entries between registered split files with backups.
   - Improve official mission creator with optional version/release selection after tester feedback.

2. Relationship Graph
   - Show full usage graph for classname across Types, Spawnabletypes parent, cargo/attachment child refs, Randompresets refs, Events, Territories, and config hints.
   - Add grouped edits for linked weapon/ammo/mag/attachment sets.
   - Add missing direct create/open helpers for linked entries.

3. Loot Distribution / Rarity Tool
   - Add charts for rarity bands, over-target relations, and usage/tier distribution.
   - Calculate and preview nominal, min, restock, spawnable chance, and preset chance changes.
   - Support linked weapon/ammo/mag/attachment scaling.
   - Export before/after balancing report.
   - Add click-through from rarity rows to Types, Spawnabletypes, Randompresets, Events, and matching mapgroup relations.

4. CE Zones Editing / areaflags.map
   - Add clearer conflict reporting when imported `.map` flags do not match current layer masks.
   - Separate compiled `.map` usage coverage from CE Tool paint/default TGA layers if more samples confirm they are different concepts.
   - Test `areaflags.map` import/export with more official/custom maps before treating writer as final.
   - Add brush shape options if needed after tester feedback.
   - Add stronger clicked-position suggestions for possible loot classes and relation conflicts.

5. Workspace Project Save / Restore
   - Save recent missions, selected map key, filters, active module, selected source files, and useful layout state.
   - Keep local-only; do not modify mission files.

6. Mapgroupproto Advanced Editing
   - Add safe forms to duplicate prototype groups.
   - Add point add/delete workflows with validation before apply.
   - Add dispatch/proxy inspection and editing while preserving unknown attributes.
   - Add click-through from containers to matching Types and from placed counts to matching `mapgrouppos.xml` groups.
   - Add before/after report for group/container/point changes.

7. Broader DayZ-Aware File Editors
   - Add stronger editors/checks for `globals.xml`, `economy.xml`, `cfgplayerspawnpoints.xml`, and selected JSON server-side mod configs.
   - Prefer DayZ-aware forms over raw generic text editing.

8. Undo / Redo Expansion
   - Extend undo/redo beyond Territory map edits.
   - Cover Types, Spawnabletypes, Randompresets, Events, Weather, Configs, and Profiles.
   - Keep changes source-aware so split files remain safe.

9. Profiles / Server Config Polish
   - Improve profile config categorization and field-specific help.
   - Add more server-host-focused validation for common JSON/XML server-side mod configs.

10. Remote Server / SFTP Support
   - Optional later feature.
   - Must include dry-run diff, backups, rollback guidance, and strong confirmation before upload.

## Release Checklist

- Run full tests: `python -m pytest --basetemp C:\tmp\rag-economy-manager-pytest`
- Build EXE: `powershell -ExecutionPolicy Bypass -File .\build_rag_economy_manager.ps1`
- Smoke-launch `dist\RaG_Economy_Manager.exe`
- Test on copied mission folder only.
- Generate Validation Report before publishing beta.
