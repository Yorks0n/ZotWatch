"""One-time centroid-only projection of frozen P5B3; not a recommendation path."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from zotwatch.interests.center_recall_contract import (
    CenterRecallModel, EncoderIdentity, FormalCentroid, V1_THRESHOLD, digest, save_center_model,
)
from zotwatch.interests.frozen_candidate import load_frozen, digest as frozen_digest


def project(snapshot):
    encoder = EncoderIdentity(model_identifier=snapshot["policy"]["model_identifier"],
        model_revision=snapshot["policy"]["model_revision"], dimension=snapshot["policy"]["dimension"],
        text_policy=snapshot["policy"]["text_policy"], artifact_sha256=snapshot["encoder_artifacts"])
    published = {group["id"]: group for group in snapshot["formation"]["qualification"]["clusters"] if group["published"]}
    centers = []
    for center in snapshot["centers"]:
        group = published[center["raw_cluster_alias"]]
        if center["model_revision"] != snapshot["model_revision"] or center["centroid"] != group["trimmed_centroid"] or group["distinct_strong_works"] < 10:
            raise ValueError("Projection requires exact qualified formal centroids")
        centers.append(FormalCentroid(interest_id=center["interest_id"], centroid=center["centroid"]))
    if len(centers) != len(published):
        raise ValueError("Formal center projection incomplete")
    return CenterRecallModel(interest_model_revision=snapshot["model_revision"],
        source_snapshot_sha256=frozen_digest(snapshot), encoder=encoder,
        embedding_text_fingerprint=digest(encoder.model_dump()), threshold=V1_THRESHOLD,
        centers=sorted(centers, key=lambda center: center.interest_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capsule", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot, _, _ = load_frozen(args.capsule)
    model = project(snapshot)
    save_center_model(args.output, model)
    print(f"Exported {len(model.centers)} formal centroids / {model.interest_model_revision} / {model.embedding_text_fingerprint}")
