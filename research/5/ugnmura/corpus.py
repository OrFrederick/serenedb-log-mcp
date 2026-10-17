"""Streaming research parser; preserve physical lines and original time semantics.

No raw input is extracted to disk. BGL ts is its *display* timestamp (unknown
timezone), epoch_s is its separate Unix-seconds field. OpenStack ts is naive.
Missing correlation ids stay None. instance_id only accepts explicit instance
tags, rather than treating every UUID (user/request/image/tenant) as a VM.
"""
from __future__ import annotations
import io
import re
import tarfile
import zipfile
from pathlib import Path

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
INSTANCE = re.compile(r"\[instance: (" + UUID + r")\]")
REQUEST = re.compile(r"\b(req-" + UUID + r")\b")
HTTP = re.compile(r'"(GET|POST|PUT|DELETE|HEAD|PATCH|OPTIONS) ([^ ]+) HTTP/[^\"]+" status: (\d+) len: (\d+) time: ([\d.]+)')
CREATE = re.compile(r"Took ([\d.]+) seconds to (.*)")
OPENSTACK = re.compile(r"^(\S+) (\d{4}-\d\d-\d\d) (\S+) (\d+) (\S+) (\S+) (?:\[([^\]]*)\] )?(.*)$")

def blank(dataset, source, line_no):
    return dict(dataset=dataset, entry_id=line_no, ts=None, node=None,
                component=None, level=None, label=None, message=None,
                source_file=source, request_id=None, instance_id=None,
                latency_s=None, rack=None, midplane=None, epoch_s=None,
                endpoint=None, http_status=None, create_seconds=None,
                create_operation=None)

def bgl_rows(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.endswith('.log')]
        if len(names) != 1:
            raise ValueError(f'Expected one BGL .log, got {names}')
        with z.open(names[0]) as raw:
            for line_no, line in enumerate(io.TextIOWrapper(raw, encoding='utf-8', errors='strict'), 1):
                parts = line.rstrip('\r\n').split(maxsplit=5)
                if len(parts) != 6:
                    row = blank('BGL', names[0], line_no)
                    row['message'] = line.rstrip('\r\n')
                    yield row
                    continue
                label, epoch, date, node, ts, remainder = parts
                # 306 entries omit the duplicated location after ts. Another
                # 10 have a corrupted duplicated location containing spaces.
                # Recover only the explicit RAS Component severity delimiter;
                # do not shift message words into Component or level columns.
                header = re.search(r'(?:^|\s)(?:RAS|NULL) (\S+) (INFO|FATAL|ERROR|WARNING|SEVERE|FAILURE)(?: (.*))?$', remainder)
                if header:
                    component, level, message = header[1], header[2], header[3] or ''
                else:
                    component = level = None
                    message = remainder
                row = blank('BGL', names[0], line_no)
                row.update(ts=ts[:10]+' '+ts[11:].replace('.', ':', 2), node=node,
                           component=component, level=level, label=label,
                           message=message, epoch_s=int(epoch))
                m = re.match(r'^(R[0-9A-Z]{2})-(M\d)', node)
                if m:
                    row.update(rack=m[1], midplane=m[1]+'-'+m[2])
                yield row

def openstack_rows(path):
    with tarfile.open(path, 'r:gz') as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith('.log'):
                continue
            raw = tar.extractfile(member)
            assert raw is not None
            for line_no, line in enumerate(io.TextIOWrapper(raw, encoding='utf-8', errors='strict'), 1):
                row = blank('OpenStack', member.name, line_no)
                match = OPENSTACK.match(line.rstrip('\r\n'))
                if not match:
                    # Continuation lines are physical Log Entries and remain in totals;
                    # no fabricated timestamp, level or correlation ids.
                    row.update(message=line.rstrip('\r\n'))
                    yield row
                    continue
                emitter, date, time, pid, level, component, context, message = match.groups()
                row.update(ts=date+' '+time, component=component, level=level,
                           message=message)
                request = REQUEST.search(context or '')
                instance = INSTANCE.search(message)
                if request:
                    row['request_id'] = request[1]
                if instance:
                    row['instance_id'] = instance[1]
                http = HTTP.search(message)
                if http:
                    # Replace tenant/instance UUID or 32-hex id with a bounded route token.
                    endpoint = re.sub(UUID, '{uuid}', http[2])
                    endpoint = re.sub(r'(?<=/)[0-9a-f]{32}(?=/|$)', '{tenant}', endpoint)
                    row.update(endpoint=http[1]+' '+endpoint, http_status=int(http[3]), latency_s=float(http[5]))
                creation = CREATE.search(message)
                if creation:
                    row.update(create_seconds=float(creation[1]), create_operation=creation[2])
                yield row

def anomaly_ids(path):
    with tarfile.open(path, 'r:gz') as tar:
        raw = tar.extractfile('anomaly_labels.txt')
        assert raw is not None
        return re.findall(UUID, raw.read().decode())
