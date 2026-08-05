# Fleet (cluster broadcast)

Fleet takes one NL intent, runs **OS-aware commands** on all open tabs, then lets you **generate a report on demand** (or schedule it).

## Closed loop

1. **🌐 Fleet** → pick hosts/groups in **target scope** (or **Open tabs only**) → optional **Diagnose** opens mobile IM with that scope → confirm preview  
2. Always-visible panel: `{done}/{total}` + progress bar; **per-host rows** (✅/❌, expand for details); failures auto-expand  
3. After finish: **tone select** + **Generate report** / **Schedule**  
4. Full report (incl. recommended actions); export **Markdown** or **PDF** (print → Save as PDF)

## Schedule

Daily/weekly + time; failure policy; notify checkboxes are **placeholders**. Due jobs run as **oneshot** on saved `host_ids` (tabs need not be open).

## Scope & groups

Default: select inventory range (auto-open missing tabs). Optional: open-tabs-only. Manage static groups via **+ → Host groups**.

See also Broadcast settings for the default report tone.
