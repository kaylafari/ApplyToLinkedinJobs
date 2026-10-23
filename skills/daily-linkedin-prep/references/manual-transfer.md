# Manual download and transfer

This is the user's selected handoff mode. No shared drive, account connection,
automatic synchronization or polling is required. Cloud prepares files; the user
downloads and moves them to the computer; the local coordinating agent verifies
and imports them. A cloud-only path is not proof of a completed download.

## Prepare the cloud download

1. Publish each packet with `cloud_handoff.py publish` as described in
   [hybrid-workflow.md](hybrid-workflow.md). Use a private staging directory outside
   the checkout containing only published application packets and their indexes.
   Do not add the mutable execution ledger, owner tokens, browser data, credentials,
   full candidate context, or the master baseline to this packet download.
2. Give the batch a unique name. Package the staging directory with the standard
   Python ZIP command below. The destination must be outside the staging directory.
   Never overwrite an earlier batch; retain immutable packet revisions.
3. Compute the ZIP's SHA256 in bounded chunks. Save the checksum alongside it and
   report the expected hash in the cloud run's handoff message. A checksum verifies
   transfer integrity; use the trusted cloud run's hash rather than trusting a
   checksum obtained from an unrelated download.
4. Offer the ZIP and checksum through the host's supported private file-download
   interface, if actually available. Verify that the offered artifact exists. Do
   not invent a downloadable URL, upload to a public service, or claim that the
   laptop received it. If the host exposes no private download interface, report
   `manual download unavailable in this host` with the actual retained file paths.
   Files must be downloaded before replacing/ending an ephemeral environment;
   a generated archive alone does not establish durable preservation.

Example commands from a supported cloud terminal; replace the uppercase paths
and BATCH label with the real private locations and a unique batch identifier:

```sh
umask 077
mkdir -p /PRIVATE/exports
python3 -m zipfile -c /PRIVATE/exports/linkedin-prep-BATCH.zip /PRIVATE/handoff
python3 - <<'PY'
import hashlib
from pathlib import Path
archive = Path('/PRIVATE/exports/linkedin-prep-BATCH.zip')
checksum = hashlib.sha256()
with archive.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        checksum.update(chunk)
line = checksum.hexdigest() + '  ' + archive.name + '\n'
archive.with_suffix('.zip.sha256').write_text(line)
print(line, end='')
PY
```

## Receive on the computer

1. Obtain the updated repository/helper code separately if it is not already on
   the computer. Packet archives contain application data, not a skill installer.
   Preserve existing private sources and history; never overwrite them with a
   fresh empty setup.
2. Download/copy the ZIP and checksum to a private local directory outside the
   checkout. Recompute SHA256 with the chunked code above using the local archive
   path and compare it with the expected hash from the trusted cloud run. Stop on
   mismatch; do not extract or import the failed download.
3. Extract only the verified archive into a **new empty private directory** using
   the computer's normal archive tool. Do not extract over an installed skill,
   existing packet revision or execution state. The example archive contains a
   `handoff/APPLICATION_ID/REVISION` directory tree. Never run downloaded job code.
4. Run `cloud_handoff.py validate --packet /PRIVATE/received/handoff/ID/REVISION`
   for each selected packet, then import it through the existing local consumer.
   The helper rechecks the manifest and declared asset hashes. Keep using the same
   private execution ledger, original applicant ID and restored prior history.
   Validate and import incrementally; downloading many packets opens no browser
   tabs and does not authorize increasing the one-application browser limit.
5. Only after successful local validation record retrieval_verified=true during
   preflight. Record the batch/hash/local verification result in private handoff
   evidence, and proceed with the local non-submitting pilot/start-resume prompt.

Duplicate batch imports preserve corrections/outcomes and never reset progress.
If interrupted, repeat validation/import through the same ledger. Keep or remove
verified local archives according to the user's existing storage practices; do not
automatically delete the only copy of a packet or unexported private state.

## Returning newly observed questions

The optional `observe-form` export is a minimal private JSON file. The user moves
or attaches that file to the cloud preparation chat through a supported private
upload. Cloud uses it to prepare a newer immutable packet; download/verify/import
that revision by the same steps. Keep the browser slot held and checkpoint
waiting-for-preparation while awaiting the return. No automatic round trip is
implied. Independent cloud preparation may continue; human review and submission
remain local and manual.
