# DeepTutor integration

## Working assumption

DeepTutor imports and manages documents as independent records. A selected directory may be processed as multiple files rather than preserved as one compound package.

Therefore, every generated import document must be self-contained.

## Intended import flow

1. Generate validated PDFs locally.
2. Transfer the export directory to the DeepTutor host.
3. Import the files through the supported UI or documented batch command.
4. Reindex after parser or knowledge-engine changes when required.
5. Verify text retrieval and image handling with a small test set.

## Integration tests to perform

- Confirm the deployed DeepTutor version.
- Confirm the selected document parser.
- Confirm whether embedded figures produce image nodes.
- Confirm whether retrieved context carries images to the answering model.
- Confirm source references remain understandable after import.
- Confirm batch import behavior for a directory.
- Confirm duplicate import handling.
- Confirm reindex behavior after document updates.

## Design rule

The preprocessing tool must not rely on successful multimodal indexing for correctness.

A figure-heavy document should remain retrievable and understandable through its text even when image indexing is absent or fails. Embedded figures remain necessary for visual fidelity and for deployments where multimodal indexing works.


