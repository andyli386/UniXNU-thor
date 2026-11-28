from pathlib import Path
import struct
import sys

thor_out = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(thor_out.parent / "UniXNU-pong/scripts"))
from pong_adt_contract import parse_adt


def emit(nodes, path):
    entries = nodes[path]
    children = [key for key in nodes if key != "/" and key.rsplit("/", 1)[0] == path.rstrip("/")]
    result = bytearray(struct.pack("<II", len(entries), len(children)))
    for key, value in entries.items():
        result += struct.pack("<32sI", key.encode("ascii"), len(value))
        result += value
        result += bytes(-len(value) % 4)
    for child in children:
        result += emit(nodes, child)
    return result


def prepare(blob):
    nodes = parse_adt(blob)
    if emit(nodes, "/") != blob:
        raise ValueError("Cannot reproduce original ADT encoding")

    chosen = nodes["/chosen"]
    if "thor-native-scanout" in chosen:
        raise ValueError("Scanout property already exists")
    chosen["thor-native-scanout"] = bytes(16)

    # Use Fold2's trace mode for transient interrupt masking. XNU resolves
    # this tunable first in /chosen/machine-timeouts, before boot arguments.
    if "/chosen/machine-timeouts" in nodes or "/machine-timeouts" in nodes:
        raise ValueError("Unexpected existing machine-timeouts ADT node")
    nodes["/chosen/machine-timeouts"] = {
        "name": b"machine-timeouts\0",
        "interrupt-masked-debug-mode": struct.pack("<I", 1),  # SCHED_HYGIENE_MODE_TRACE
    }

    # Fold2's XNU AMP contract uses an ASCII code encoded as a uint32.
    # These are scheduling clusters. Preserve the hardware affinities in reg.
    cpu_properties = {key for key in nodes if key.rsplit("/", 1)[0] == "/cpus" and
                      nodes[key].get("device_type") == b"cpu\0"}
    expected = {f"/cpus/cpu{cpu}" for cpu in range(8)}
    if cpu_properties != expected:
        raise ValueError("Thor ADT must contain the eight expected CPU nodes")
    for cpu in range(8):
        entry = nodes[f"/cpus/cpu{cpu}"]
        if entry.get("reg") != struct.pack("<I", cpu << 8):
            raise ValueError(f"Unexpected hardware affinity for CPU {cpu}")
        if "cluster-type" in entry:
            raise ValueError(f"CPU {cpu} already has a scheduling cluster")
        entry["cluster-type"] = struct.pack("<I", ord("E" if cpu < 4 else "P"))

    packed = emit(nodes, "/")
    if parse_adt(packed) != nodes:
        raise ValueError("Thor AMP properties failed ADT round-trip")
    return packed


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_bytes(prepare(path.read_bytes()))
