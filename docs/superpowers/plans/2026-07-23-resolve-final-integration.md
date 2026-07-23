# Resolve Final Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply successful `transcribe` and `narrate` jobs to new, editable DaVinci Resolve timelines without changing beat-sync, then execute the approved local consolidated acceptance.

**Architecture:** Preserve the beat-sync-only `AdapterService` and `load_validated_job()` path. Add a strict media contract plus a separate `MediaPlacementService`; it reuses the existing gateway, attempt store, locking, non-destructive names, and Tk Utility. It stages verified source media, creates V1/A1 at record frame zero, then pauses until the user imports the verified SRT through Resolve’s standard UI.

**Tech Stack:** Python 3.12, uv, pytest, DaVinci Resolve in-process Python API, Tkinter, local FFmpeg/faster-whisper/VOICEVOX.

## Global Constraints

- Work locally on Windows. Do not fetch, push, create a PR, add cloud/OpenAI/Web automation, or start VOICEVOX.
- Preserve every beat-sync contract, UI flow, test, Resolve bin/timeline/marker behavior, and legacy MinoruDouga installation.
- Never overwrite/delete an input, successful artifact, existing Resolve bin, or existing Resolve timeline. Attempts use `_MinoruStudio …` bins and unique `-002` timelines.
- Before Resolve mutation, rehash every manifest input and required artifact. Reject escaped paths, directories, missing/bad size/hash, and unsupported media.
- Product time remains integer milliseconds; Resolve frame conversion stays inside the existing gateway.
- Transcribe puts source video on V1 and the same verified source on A1. Narrate puts source video on V1 and verified `outputs/narration.wav` on A1. Both begin at record frame 0.
- Subtitle placement is manual standard SRT import only. Do not call `CreateSubtitlesFromAudio`, generate subtitle clips, or use preview media in place of source media.
- UI/log/error text must not include script, transcript, or SRT cue content. IDs, hashes, paths, names, rates, and fixed labels are allowed metadata.
- Real acceptance uses only a newly created disposable Resolve project. No final render, external send, deletion, or non-local service is in scope.

---

## File structure and interfaces

| File | Responsibility |
|---|---|
| `resolve_adapter/minoru_studio_resolve/contract.py` | Keep beat-sync `load_validated_job()` unchanged; add strict `load_validated_media_job()`. |
| `resolve_adapter/minoru_studio_resolve/gateway.py` | Add generic ID-checked media staging, V1/A1 placement, timeline lookup, and subtitle-track inspection. |
| `resolve_adapter/minoru_studio_resolve/media_service.py` | New transcribe/narrate stateful Resolve application flow. |
| `resolve_adapter/minoru_studio_resolve/state.py` | Add the media checkpoint transition/action only. |
| `resolve_adapter/minoru_studio_resolve/job_io.py` | Persist optional mode/subtitle metadata compatibly. |
| `resolve_adapter/minoru_studio_resolve/ui.py` | Select beat/media service and expose manual subtitle checkpoint. |
| `tests/resolve_adapter/conftest.py` | Fingerprinted successful transcribe/narrate fixtures. |
| `tests/resolve_adapter/fakes.py` | Public fake Resolve subtitle-track calls. |
| `tests/resolve_adapter/test_media_contract.py` | Media contract and pre-mutation tamper tests. |
| `tests/resolve_adapter/test_media_gateway.py` | Stage and V1/A1 descriptor tests. |
| `tests/resolve_adapter/test_media_service.py` | State, cancellation, failure, `-002`, and sentinel tests. |
| `tests/resolve_adapter/test_ui_state.py` | UI checkpoint action/instruction/confirmation tests. |
| `README.md`, `docs/agent-handoff.md`, `docs/resolve-transcribe-narrate-acceptance.md` | Workflow, handoff status, and acceptance runbook/result. |

`load_validated_media_job(job_dir)` returns exactly:

    {
        "root": str,
        "manifest": dict,
        "mode": "transcribe" | "narrate",
        "timeline_name": "<job name> Resolve",
        "sources": [
            {"key": "source-video", "kind": "video", "path": str},
            {"key": "source-audio" | "narration-audio", "kind": "audio", "path": str},
        ],
        "subtitle": {"path": str, "size": int, "sha256": str},
    }

`MediaPlacementService.start()`, `apply_ready()`, `confirm_subtitle_import()`, and `latest_detail()` return the current JSON-compatible application detail. New fields are `mode`, `subtitle`, `timeline`, and `result`; existing beat-sync fields retain their meanings.

### Task 1: Define strict media contracts and fixtures

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/contract.py`
- Modify: `tests/resolve_adapter/conftest.py`
- Create: `tests/resolve_adapter/test_media_contract.py`
- Test: `tests/resolve_adapter/test_contract.py`

**Interfaces:**
- Consumes: successful v1 job manifests and existing input/artifact fingerprints.
- Produces: `load_validated_media_job(job_dir) -> dict`; invalid state raises `ContractError` before a Resolve fake/gateway is used.

- [ ] **Step 1: Write failing succeeded-job fixtures and contract tests**

Add the following fixture builders to `tests/resolve_adapter/conftest.py`; they must use `JobStore.create`, write each output, fingerprint it with `fingerprint_artifact`, and set `JobStatus.SUCCEEDED`.

    def build_prepared_transcribe_job(tmp_path):
        source = tmp_path / "source.mp4"; source.write_bytes(b"video")
        job = JobStore().create(tmp_path / "jobs", "transcribe demo", JobMode.TRANSCRIBE, [source])
        records = []
        for kind, name in (("transcript-txt", "transcript.txt"), ("subtitles-srt", "subtitles.srt"), ("subtitles-vtt", "subtitles.vtt")):
            path = job / "outputs" / name; path.write_bytes(kind.encode("ascii"))
            records.append(fingerprint_artifact(job, path, kind))
        JobStore().update(job, lambda m: (setattr(m, "status", JobStatus.SUCCEEDED), setattr(m, "artifacts", records)))
        return job

    def build_prepared_narrate_job(tmp_path):
        video = tmp_path / "source.mp4"; script = tmp_path / "script.txt"
        video.write_bytes(b"video"); script.write_bytes(b"approved script")
        job = JobStore().create(tmp_path / "jobs", "narrate demo", JobMode.NARRATE, [video, script])
        records = []
        for kind, name in (("narration-wav", "narration.wav"), ("subtitles-srt", "subtitles.srt"), ("subtitles-vtt", "subtitles.vtt")):
            path = job / "outputs" / name; path.write_bytes(kind.encode("ascii"))
            records.append(fingerprint_artifact(job, path, kind))
        JobStore().update(job, lambda m: (setattr(m, "status", JobStatus.SUCCEEDED), setattr(m, "artifacts", records)))
        return job

Expose `prepared_transcribe_job` and `prepared_narrate_job` fixtures. Create `test_media_contract.py` with:

    def test_transcribe_reuses_verified_source_for_v1_and_a1(prepared_transcribe_job):
        loaded = load_validated_media_job(prepared_transcribe_job)
        assert loaded["mode"] == "transcribe"
        assert [item["key"] for item in loaded["sources"]] == ["source-video", "source-audio"]
        assert loaded["sources"][0]["path"] == loaded["sources"][1]["path"]

    def test_narrate_uses_verified_narration_for_a1(prepared_narrate_job):
        loaded = load_validated_media_job(prepared_narrate_job)
        assert [item["key"] for item in loaded["sources"]] == ["source-video", "narration-audio"]
        assert loaded["sources"][1]["path"].endswith("narration.wav")

Add parameterized negative tests for changed source, changed narration WAV, absent SRT record, duplicate required artifact kind, `../outside.srt`, directory artifact, `.wav` source input, and `script-draft` mode. Each uses `pytest.raises(ContractError)` and never constructs Resolve.

- [ ] **Step 2: Verify failure before implementation**

Run: `uv run pytest tests/resolve_adapter/test_media_contract.py -q`

Expected: collection/import fails because `load_validated_media_job` is absent.

- [ ] **Step 3: Implement media-only validation**

Leave `load_validated_job()` unchanged. Add these helpers above the new public loader:

    _RESOLVE_VIDEO_SUFFIXES = frozenset((".avi", ".mkv", ".mov", ".mp4", ".mxf", ".webm"))

    def _regular_file(path, label):
        try:
            stat = os.stat(path)
        except OSError as exc:
            raise ContractError("{0} is missing: {1}".format(label, exc))
        if not os.path.isfile(path):
            raise ContractError("{0} must be a regular file".format(label))
        return stat

    def _validated_inputs(manifest):
        inputs = manifest.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ContractError("job inputs are missing")
        for expected in inputs:
            if not isinstance(expected, dict):
                raise ContractError("job input must be an object")
            try:
                path = os.path.realpath(expected["path"]); stat = _regular_file(path, "input")
                if stat.st_size != expected["size"] or stat.st_mtime_ns != expected["mtime_ns"] or sha256_file(path) != expected["sha256"]:
                    raise ContractError("input changed: {0}".format(path))
            except KeyError as exc:
                raise ContractError("input is incomplete: {0}".format(exc))
        return inputs

    def _required_artifact(root, artifacts, kind, relative):
        matches = [item for item in artifacts if isinstance(item, dict) and item.get("kind") == kind]
        if len(matches) != 1:
            raise ContractError("job must contain one {0}".format(kind))
        record = matches[0]
        try:
            path = contained_path(root, record["path"])
            if os.path.normcase(os.path.relpath(path, root)) != os.path.normcase(relative.replace("/", os.sep)):
                raise ContractError("{0} path is invalid".format(kind))
            stat = _regular_file(path, kind)
            if stat.st_size != record["size"] or sha256_file(path) != record["sha256"]:
                raise ContractError("{0} fingerprint mismatch".format(kind))
        except KeyError as exc:
            raise ContractError("{0} record is incomplete: {1}".format(kind, exc))
        return {"path": path, "size": stat.st_size, "sha256": record["sha256"]}

Implement `load_validated_media_job` with this exact mode policy:

    root = os.path.realpath(job_dir)
    manifest = _read_object(os.path.join(root, "job.json"))
    if manifest.get("schema_version") != 1 or manifest.get("status") != "succeeded":
        raise ContractError("job is not successful")
    inputs = _validated_inputs(manifest)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ContractError("job artifacts must be a list")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContractError("job name must be non-empty")
    if manifest.get("mode") == "transcribe" and len(inputs) == 1:
        video = inputs[0]["path"]; audio_key = "source-audio"; audio_path = video
        required = (("transcript-txt", "outputs/transcript.txt"), ("subtitles-srt", "outputs/subtitles.srt"), ("subtitles-vtt", "outputs/subtitles.vtt"))
    elif manifest.get("mode") == "narrate" and len(inputs) == 2:
        video = inputs[0]["path"]; audio_key = "narration-audio"; audio_path = None
        required = (("narration-wav", "outputs/narration.wav"), ("subtitles-srt", "outputs/subtitles.srt"), ("subtitles-vtt", "outputs/subtitles.vtt"))
    else:
        raise ContractError("job mode is not supported by Resolve media placement")
    if os.path.splitext(video)[1].lower() not in _RESOLVE_VIDEO_SUFFIXES:
        raise ContractError("source input is not a Resolve-readable video")
    verified = {kind: _required_artifact(root, artifacts, kind, relative) for kind, relative in required}
    if audio_path is None:
        audio_path = verified["narration-wav"]["path"]
    return {
        "root": root, "manifest": manifest, "mode": manifest["mode"],
        "timeline_name": "{0} Resolve".format(name),
        "sources": [{"key": "source-video", "kind": "video", "path": video}, {"key": audio_key, "kind": "audio", "path": audio_path}],
        "subtitle": verified["subtitles-srt"],
    }

- [ ] **Step 4: Run contract regressions**

Run: `uv run pytest tests/resolve_adapter/test_contract.py tests/resolve_adapter/test_media_contract.py -q`

Expected: pass; every tamper/escape/non-regular input is rejected before Resolve mutation.

- [ ] **Step 5: Commit**

    git add resolve_adapter/minoru_studio_resolve/contract.py tests/resolve_adapter/conftest.py tests/resolve_adapter/test_media_contract.py tests/resolve_adapter/test_contract.py
    git commit -m "feat: validate Resolve media placement jobs"

### Task 2: Add generic gateway staging and V1/A1 placement

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/gateway.py`
- Modify: `tests/resolve_adapter/fakes.py`
- Create: `tests/resolve_adapter/test_media_gateway.py`
- Test: `tests/resolve_adapter/test_apply.py`, `tests/resolve_adapter/test_placement.py`

**Interfaces:**
- Consumes: Task 1 `sources`.
- Produces: `import_media_sources`, `find_media_items`, `place_media_timeline`, `timeline_by_id`, and `subtitle_track_count`.

- [ ] **Step 1: Write failing gateway tests**

Create `test_media_gateway.py` with contract-shaped `validated` fixture data and a fresh fake bin/timeline. Test:

    def test_transcribe_places_same_item_on_v1_and_a1():
        details = gateway.import_media_sources(validated["sources"], validated["bin"])
        items = gateway.find_media_items(validated["bin"], details)
        result = gateway.place_media_timeline(timeline, items, validated)
        video = timeline.video_track_items[1][0]; audio = timeline.audio_track_items[1][0]
        assert video.media_pool_item.GetUniqueId() == audio.media_pool_item.GetUniqueId()
        assert video.record_frame == audio.record_frame == 0
        assert result["audio_key"] == "source-audio"

    def test_narrate_places_generated_narration_on_a1():
        details = gateway.import_media_sources(validated["sources"], validated["bin"])
        gateway.place_media_timeline(timeline, gateway.find_media_items(validated["bin"], details), validated)
        assert timeline.video_track_items[1][0].media_pool_item.GetName() == "source.mp4"
        assert timeline.audio_track_items[1][0].media_pool_item.GetName() == "narration.wav"

Also test missing/ambiguous ID, empty append result, `timeline_by_id`, and subtitle count before/after `timeline.AddTrack("subtitle")`.

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_media_gateway.py -q`

Expected: fails because generic media gateway methods are absent.

- [ ] **Step 3: Add only fake public subtitle behavior**

Add to `FakeTimeline.__init__`:

    self.subtitle_track_count = 0

and methods:

    def AddTrack(self, track_type):
        if track_type != "subtitle":
            return False
        self.subtitle_track_count += 1
        return True

    def GetTrackCount(self, track_type):
        if track_type == "subtitle":
            return self.subtitle_track_count
        if track_type == "video":
            return len(self.video_track_items)
        if track_type == "audio":
            return len(self.audio_track_items)
        return 0

Do not fake SRT import or change beat-sync probe behavior.

- [ ] **Step 4: Implement gateway methods in isolation**

Do not modify existing `import_inputs`, `find_items`, or `populate_timeline`.

    def import_media_sources(self, sources, bin_detail):
        # Select the recorded bin. Require each nonblank key/path and kind video/audio.
        # Deduplicate normalized paths before ImportMedia: transcribe maps two roles to one item.
        # Reuse _match_imports. Return one detail per role:
        # {"key", "kind", "id", "path", "frames", "fps"}.
        # Reject Frames <= 0 before return.

    def find_media_items(self, bin_detail, item_details):
        # Walk only application_bin, map stable item IDs, require every role key once,
        # and return {key: media_pool_item}; otherwise raise GatewayError.

    def place_media_timeline(self, timeline, items, validated):
        # source-video plus source-audio for transcribe or narration-audio for narrate.
        # Append exactly once to V1 then A1 using:
        # startFrame=0, endFrame=Frames-1, trackIndex=1,
        # recordFrame=int(timeline.GetStartFrame()), mediaType=1 then 2.
        # Use _append and return {"video_key", "audio_key", "record_frame"}.

    def timeline_by_id(self, timeline_id):
        # Scan GetTimelineByIndex(1..GetTimelineCount()) by GetUniqueId;
        # missing means GatewayError("recorded final timeline is missing").

    def subtitle_track_count(self, timeline):
        # return int(timeline.GetTrackCount("subtitle")); wrap invalid API values in GatewayError.

Store only safe IDs/keys/paths/rate/frames; never subtitle content.

- [ ] **Step 5: Run gateway and beat-sync placement regressions**

Run: `uv run pytest tests/resolve_adapter/test_media_gateway.py tests/resolve_adapter/test_placement.py tests/resolve_adapter/test_apply.py -q`

Expected: pass; legacy markers, A1/V1, and `-002` tests remain green.

- [ ] **Step 6: Commit**

    git add resolve_adapter/minoru_studio_resolve/gateway.py tests/resolve_adapter/fakes.py tests/resolve_adapter/test_media_gateway.py
    git commit -m "feat: stage Resolve media placement sources"

### Task 3: Add media state, persistence, and service

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/state.py`
- Modify: `resolve_adapter/minoru_studio_resolve/job_io.py`
- Create: `resolve_adapter/minoru_studio_resolve/media_service.py`
- Create: `tests/resolve_adapter/test_media_service.py`
- Test: `tests/resolve_adapter/test_state.py`, `tests/resolve_adapter/test_job_io.py`

**Interfaces:**
- Consumes: Tasks 1–2 plus existing application lock/claim/release/reconcile.
- Produces: `MediaPlacementService.start`, `apply_ready`, `confirm_subtitle_import`, and `latest_detail`.

- [ ] **Step 1: Write failing state/service tests**

Use deterministic IDs/tokens like `test_apply.py`. Add:

    def test_transcribe_places_then_waits_for_manual_srt(prepared_transcribe_job):
        resolve, service = make_media_service()
        assert service.start(str(prepared_transcribe_job))["state"] == "ready"
        detail = service.apply_ready(str(prepared_transcribe_job), confirm=lambda _: True)
        assert detail["state"] == "awaiting_subtitle_import"
        timeline = resolve.project.final_timelines[0]
        assert len(timeline.video_track_items[1]) == len(timeline.audio_track_items[1]) == 1
        assert detail["timeline"]["id"] == timeline.GetUniqueId()

    def test_subtitle_confirmation_requires_manual_track(prepared_narrate_job):
        resolve, service = make_media_service()
        service.start(str(prepared_narrate_job)); service.apply_ready(str(prepared_narrate_job), confirm=lambda _: True)
        with pytest.raises(AdapterError, match="subtitle track"):
            service.confirm_subtitle_import(str(prepared_narrate_job), confirm=lambda _: True)
        assert service.latest_detail(str(prepared_narrate_job))["state"] == "awaiting_subtitle_import"
        assert resolve.project.final_timelines[0].AddTrack("subtitle")
        assert service.confirm_subtitle_import(str(prepared_narrate_job), confirm=lambda _: True)["state"] == "applied"

Also test: declined timeline confirmation remains `ready` without timeline; declined SRT confirmation remains checkpoint; terminal requires `new_attempt=True`; second attempt gets `<job> Resolve-002`; append failure retains partial new timeline and fails attempt; changed SRT/missing timeline/no subtitle track retain checkpoint; sentinel never changes; `next_action` is `confirm_subtitles`.

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_media_service.py tests/resolve_adapter/test_state.py -q`

Expected: fails because service/state are absent.

- [ ] **Step 3: Add minimal shared transitions**

Preserve existing state edges and add:

    "staging": frozenset(("awaiting_in_out", "checking_still", "ready", "failed")),
    "applying": frozenset(("applied", "awaiting_subtitle_import", "failed")),
    "awaiting_subtitle_import": frozenset(("applying", "failed")),

Add `"awaiting_subtitle_import": "confirm_subtitles"` to `next_action`. Never jump directly from checkpoint to `applied`.

- [ ] **Step 4: Persist safe fields compatibly**

Change `ApplicationStore.create` to accept `mode="beat-sync", subtitle=None`, then include:

    "mode": str(mode),
    "subtitle": subtitle,

in the application detail. Existing beat-sync service keeps defaults. Media service passes:

    {
        "path": validated["subtitle"]["path"],
        "size": validated["subtitle"]["size"],
        "sha256": validated["subtitle"]["sha256"],
        "user_confirmed": False,
    }

Do not add it to the manifest summary and do not read cue content.

- [ ] **Step 5: Implement independent `MediaPlacementService`**

Create `media_service.py` by copying `AdapterService`’s failure/log/claim/release skeleton only. Required behavior:

    start(job_dir, new_attempt=False)
      - Validate with load_validated_media_job before current_project/bin creation.
      - Refuse unfinished current attempt; terminal attempt requires new_attempt=True.
      - create(mode/subtitle), claim, record product, create application bin,
        import sources, record project rate, transition staging -> ready, log/release.

    latest_detail(job_dir)
      - Revalidate contract, get current Resolve project, return latest attempt
        or raise AdapterError("no Resolve application exists for this project").

    apply_ready(job_dir, confirm)
      - Require ready and unclaimed state. Use proposed unique timeline name.
      - Call confirm with mode/timeline_name/audio_label/subtitle_path only.
      - False returns unchanged ready detail.
      - Claim; ready -> applying; create/record final timeline; verify rate;
        find staged IDs; place V1/A1; save result;
        applying -> awaiting_subtitle_import; update/log/release.

    confirm_subtitle_import(job_dir, confirm)
      - Require awaiting_subtitle_import. False returns unchanged detail.
      - Revalidate job; require current SRT path/size/hash equal stored subtitle.
      - Claim; checkpoint -> applying; resolve timeline by recorded stable ID;
        require subtitle_track_count >= 1; set user_confirmed=True;
        applying -> applied; update/log/release.

Missing timeline, no subtitle track, cancellation, or SRT mismatch leave `awaiting_subtitle_import`; they are checkpoint failures, not `applications.fail`. Other claimed-operation exceptions call `applications.fail`, retain all created Resolve objects, append a content-free log event, and raise `AdapterError`.

- [ ] **Step 6: Run stateful regressions**

Run: `uv run pytest tests/resolve_adapter/test_media_service.py tests/resolve_adapter/test_state.py tests/resolve_adapter/test_job_io.py tests/resolve_adapter/test_apply.py tests/resolve_adapter/test_resume_and_still.py -q`

Expected: pass; beat-sync never gets subtitle checkpoint behavior.

- [ ] **Step 7: Commit**

    git add resolve_adapter/minoru_studio_resolve/state.py resolve_adapter/minoru_studio_resolve/job_io.py resolve_adapter/minoru_studio_resolve/media_service.py tests/resolve_adapter/test_media_service.py tests/resolve_adapter/test_state.py tests/resolve_adapter/test_job_io.py
    git commit -m "feat: apply transcribe and narrate jobs in Resolve"

### Task 4: Dispatch safely from the Resolve Utility UI

**Files:**
- Modify: `resolve_adapter/minoru_studio_resolve/ui.py`
- Modify: `tests/resolve_adapter/test_ui_state.py`
- Test: `tests/resolve_adapter/test_entry.py`

**Interfaces:**
- Consumes: fixed `job.json["mode"]`, existing `AdapterService`, and Task 3 service.
- Produces: one Utility window with an explicit safe action for beat-sync, transcribe, or narrate.

- [ ] **Step 1: Write failing UI tests**

Add:

    ("awaiting_subtitle_import", ("字幕読み込みを確認", "confirm_subtitles", False)),

to the action table. Add tests requiring instruction text to contain `Resolve` and `字幕`; add safe confirmation test:

    message = confirmation_text({
        "mode": "narrate", "timeline_name": "narrate demo Resolve",
        "audio_label": "生成ナレーション", "subtitle_path": "C:/job/outputs/subtitles.srt",
    })
    assert "生成ナレーション" in message
    assert "subtitles.srt" in message
    assert "既存のタイムラインやビンは上書きしません" in message

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/resolve_adapter/test_ui_state.py -q`

Expected: fails because media action/summary do not exist.

- [ ] **Step 3: Implement dispatch and the checkpoint**

In `AdapterWindow.__init__`, create both services with shared gateway/store:

    self.beat_service = AdapterService(self.gateway, self.applications)
    self.media_service = MediaPlacementService(self.gateway, self.applications)
    self.service = None

In `refresh()`:

    if manifest.get("mode") == "beat-sync":
        self.service = self.beat_service
    elif manifest.get("mode") in ("transcribe", "narrate"):
        self.service = self.media_service
    else:
        raise ValueError("このジョブモードはResolve適用に対応していません")

Extend `action_for_detail`/`instruction_for_detail` for `awaiting_subtitle_import`. Instruction must direct standard SRT import, editable track verification, rerunning Utility, and `字幕読み込みを確認`, without showing file content.

Keep legacy confirmation branch unchanged. Add media confirmation:

    "新しいタイムラインを生成します。\n\n"
    "名前: {timeline_name}\nV1: 元動画\nA1: {audio_label}\n"
    "字幕: Resolveで手動読み込み\n\n"
    "既存のタイムラインやビンは上書きしません。続行しますか？"

Dispatch `confirm_subtitles` to `self.service.confirm_subtitle_import(path, confirm=self._confirm_subtitles)`. `_confirm_subtitles` asks only whether manual import is complete. When `apply` returns checkpoint state, show the instruction and close Utility just like In/Out/still checkpoints so Resolve receives user interaction.

- [ ] **Step 4: Run UI/entry regressions**

Run: `uv run pytest tests/resolve_adapter/test_ui_state.py tests/resolve_adapter/test_entry.py -q`

Expected: pass; current beat-sync labels and confirmation stay unchanged.

- [ ] **Step 5: Commit**

    git add resolve_adapter/minoru_studio_resolve/ui.py tests/resolve_adapter/test_ui_state.py tests/resolve_adapter/test_entry.py
    git commit -m "feat: expose Resolve subtitle import checkpoint"

### Task 5: Update user workflow and runbook

**Files:**
- Modify: `README.md`
- Modify: `docs/agent-handoff.md`
- Create: `docs/resolve-transcribe-narrate-acceptance.md`

**Interfaces:**
- Consumes: finished Utility behavior and the safety style in `docs/resolve-beat-sync-acceptance.md`.
- Produces: accurate workflow prose and a runbook that says `未実行` until observation.

- [ ] **Step 1: Create expected-result table first**

Create the runbook sections `目的`, `安全条件`, `事前確認`, `実行手順`, `期待結果`, `実行記録`. Its table lists: transcribe V1/source A1; narrate V1/narration A1; record frame zero; editable manual subtitle track; sentinel/existing bin/timeline unchanged; `-002`; real-video script-draft; actual VOICEVOX narrate preview; unchanged source hash. Every cell begins `未実行`.

- [ ] **Step 2: Document the bounded Resolve workflow**

After transcribe in `README.md`, add `## Resolveで文字起こし・読み上げを適用する`:

1. Select successful video transcribe/narrate job in **ワークスペース → スクリプト → MinoruStudio**.
2. Transcribe gets V1 source and A1 source; narrate gets V1 source and A1 `outputs/narration.wav`.
3. Utility creates `<job name> Resolve` or `-002`; it never renders/replaces/deletes existing timeline.
4. Manually import the displayed verified `outputs/subtitles.srt` through standard Resolve UI; check editable subtitle track; rerun Utility and click `字幕読み込みを確認`.
5. Adapter never auto-captions or uses preview media as source.

Add short narrate usage if absent, including loopback VOICEVOX only and “Resolve adapter never starts it.”

- [ ] **Step 3: Add exact real-acceptance preparation**

Runbook commands:

    uv run minoru-studio doctor --json
    uv run minoru-studio script-draft .\disposable-source.mp4 -Name final-script-draft -OutputDir .\acceptance-jobs
    uv run minoru-studio narrate .\disposable-source.mp4 .\approved-script.txt -Name final-narrate -OutputDir .\acceptance-jobs -Preview
    uv run minoru-studio transcribe .\disposable-source.mp4 -Name final-transcribe -OutputDir .\acceptance-jobs -Preview

Require already-running loopback VOICEVOX, source hashes before/after, a new disposable project/sentinel, separate adapter runs, manual SRT import, and new attempt per media job. Records may include tool versions, hashes, IDs/names/rates, and pass/fail but never script/cue/transcript/audio contents.

- [ ] **Step 4: Update handoff only after mechanical pass**

After Task 6, state transcribe/narrate Resolve is `実装済み。実機まとめて受入待ち` and link this runbook. Keep script-draft/narrate real acceptance pending until Task 7 records observed facts.

- [ ] **Step 5: Check docs and commit**

Run: `uv run python -c "from pathlib import Path; assert Path('docs/resolve-transcribe-narrate-acceptance.md').is_file(); assert '字幕読み込みを確認' in Path('README.md').read_text(encoding='utf-8')"`

Expected: exits 0.

    git add README.md docs/agent-handoff.md docs/resolve-transcribe-narrate-acceptance.md
    git commit -m "docs: add Resolve media placement runbook"

### Task 6: Mechanical gates and one bounded independent audit

**Files:**
- Modify only for a blocking test/audit contradiction of the approved spec.
- Test: Resolve adapter suite, relevant mode tests, full suite, compilation, whitespace.

**Interfaces:**
- Consumes: Tasks 1–5 and `docs/superpowers/specs/2026-07-23-resolve-final-design.md`.
- Produces: mechanical readiness, not a real Resolve/VOICEVOX acceptance claim.

- [ ] **Step 1: Run adapter suite**

Run: `uv run pytest tests/resolve_adapter -q`

Expected: all current and new adapter tests pass.

- [ ] **Step 2: Run global gates**

Run:

    uv run pytest tests/test_transcribe* tests/test_narrate* -q
    uv run pytest -q
    uv run python -m compileall -q src resolve_adapter
    git diff --check

Expected: pass except documented pre-existing skips; compile exits 0; diff check has no output.

- [ ] **Step 3: Do one checklist-only independent audit**

Provide one independent reviewer only the approved spec, final diff, command outcomes, and:

    (1) beat-sync load/placement/UI unchanged;
    (2) every media input/required artifact verified before mutation;
    (3) V1/A1 and record frame correct;
    (4) no automatic subtitle API and manual track is required;
    (5) token/terminal/new-attempt/-002/partial-failure/sentinel tests;
    (6) UI/logs cannot expose script/transcript/SRT content.

It returns only blocking findings or `no blocking findings`; it must not reread the repository.

- [ ] **Step 4: Correct only blocking findings**

For each finding, add a focused failing regression, make minimum correction, rerun focused test and `uv run pytest tests/resolve_adapter -q`, then commit. If no finding blocks acceptance, create no change.

### Task 7: Execute approved disposable acceptance and record observations

**Files:**
- Modify: `docs/resolve-transcribe-narrate-acceptance.md`
- Modify: `docs/agent-handoff.md`
- Test: local MinoruStudio, user-started loopback VOICEVOX, disposable local Resolve project.

**Interfaces:**
- Consumes: Task 6 passing gates and human-operated Resolve SRT import.
- Produces: observed pass/fail/stopped record; never changes existing MinoruDouga or existing Resolve work.

- [ ] **Step 1: Reconfirm mutation scope**

Immediately before real commands, confirm the new discardable local project only; only new `_MinoruStudio …` bins/timelines; no delete/overwrite; no VOICEVOX startup/external sending; existing MinoruDouga untouched. Do not proceed without this confirmation.

- [ ] **Step 2: Record safe preflight**

Run doctor, capture versions, source/artifact hashes, job/attempt/timeline IDs/names, rates, and results only. Confirm VOICEVOX is already on `127.0.0.1` and short Japanese script approved; do not copy text/audio.

- [ ] **Step 3: Run three local preparation flows**

Use short disposable video with Task 5 commands. Inspect manifest status/artifact metadata only and compare source hashes. If a local dependency is missing, stop and record error; do not install or widen scope without user direction.

- [ ] **Step 4: Apply both jobs in Resolve with manual SRT import**

Create sentinel first. For each job, approve new timeline, inspect V1/A1/start frame, close checkpoint Utility, manually import verified SRT, confirm editable subtitle track, rerun Utility, click `字幕読み込みを確認`.

- [ ] **Step 5: Verify non-destruction/reapply**

Create new attempt per job. Verify base plus `-002`, unchanged sentinel/previous bins/timelines, and unchanged source hashes. Stop at a safety violation; do not delete Resolve objects to clean up.

- [ ] **Step 6: Record observed facts**

Change each `未実行` to `合格`, `不合格`, or `中止`, with date and content-free metadata. Mark handoff `実機まとめて受入済み` only if all pass.

    git add docs/resolve-transcribe-narrate-acceptance.md docs/agent-handoff.md
    git commit -m "docs: record Resolve final acceptance"

## Plan self-review

- **Spec coverage:** Tasks 1–3 implement contract, V1/A1, state, manual SRT, error, and non-destruction requirements. Task 4 limits UI behavior. Task 5 documents workflow/runbook. Task 6 is the required mechanical gate plus one bounded audit. Task 7 covers the combined script-draft/narrate/transcribe/Resolve evidence.
- **Placeholder scan:** No implementation has a deferred marker. `未実行` is an intentional evidence state, not a claimed result.
- **Type consistency:** Contract source fields are `key/kind/path`; gateway returns those keys; media service consumes the contract result; `awaiting_subtitle_import` maps to `confirm_subtitles` in state, service tests, and UI.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-23-resolve-final-integration.md`. Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, and keep the final audit bounded to this plan.
2. **Inline Execution** — execute tasks in this session in bounded batches with review checkpoints.

Choose one approach before implementation begins.

