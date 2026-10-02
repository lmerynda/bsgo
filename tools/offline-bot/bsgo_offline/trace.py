"""Nonblocking telemetry journal with rotating files and verified ZIP packages.

Completed 64,000-event packages become verified ZIP archives; the active package
keeps raw JSONL segments. Logging failures remain visible and never control input.
"""
from __future__ import annotations

from collections import deque
import json
import math
from pathlib import Path
import threading
import time
import uuid
import zipfile


class TraceJournal:
    def __init__(self, directory, *, queue_capacity=64000, package_capacity=64000,
                 max_file_bytes=8*1024*1024, max_event_bytes=64*1024):
        for name,value in [('queue_capacity',queue_capacity),('max_file_bytes',max_file_bytes),
                           ('max_event_bytes',max_event_bytes),('package_capacity',package_capacity)]:
            if type(value) is not int or value<=0:
                raise ValueError(f'{name} must be a positive integer')
        self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.queue_capacity=queue_capacity
        self.max_file_bytes=max_file_bytes
        self.max_event_bytes=max_event_bytes
        self.package_capacity=package_capacity
        self.run_id=uuid.uuid4().hex
        self._condition=threading.Condition()
        self._queue=deque()
        self._submitted=self._accepted=self._written=self._dropped=0
        self._closing=self._finished=False
        self._error=None
        self._segments=deque(maxlen=128)
        self._segment_count=0
        self._first_segment=None
        self._archives=deque(maxlen=128)
        self._archive_count=self._archived_events=self._active_package_events=0
        self._thread=threading.Thread(target=self._run,name='bsgo-trace-writer',daemon=True)
        self._thread.start()

    def submit(self,event):
        """Copy an event into a bounded queue; no filesystem calls occur here."""
        try:
            if not isinstance(event,dict):
                raise TypeError('Journal events must be dictionaries')
            payload=json.dumps(event,allow_nan=False,separators=(',',':'),ensure_ascii=False)
            if len(payload.encode('utf-8'))>self.max_event_bytes:
                raise ValueError('Journal event exceeds its byte limit')
        except (TypeError,ValueError,OverflowError):
            with self._condition:
                self._submitted+=1
                self._dropped+=1
            return False
        with self._condition:
            self._submitted+=1
            if self._closing or self._error or len(self._queue)>=self.queue_capacity:
                self._dropped+=1
                return False
            line=(json.dumps({'journal_sequence':self._submitted,'recorded_at':time.monotonic()},
                             separators=(',',':'))[:-1]+',"payload":'+payload+'}\n').encode('utf-8')
            self._queue.append(line)
            self._accepted+=1
            self._condition.notify()
            return True

    def stats(self):
        with self._condition:
            return {'run_id':self.run_id,'submitted':self._submitted,'accepted':self._accepted,
                    'written':self._written,'dropped':self._dropped,
                    'unwritten':self._accepted-self._written,'pending':len(self._queue),
                    'queue_capacity':self.queue_capacity,'max_file_bytes':self.max_file_bytes,
                    'closed':self._closing,'write_complete':self._finished and not self._error and
                       self._dropped==0 and self._accepted==self._written,
                    'error':self._error,'segments':tuple(self._segments),
                    'segments_scope':'recent_segment_names','segment_count':self._segment_count,
                    'first_segment':self._first_segment,
                    'last_segment':self._segments[-1] if self._segments else None,
                    'package_capacity':self.package_capacity,'active_package_events':self._active_package_events,
                    'archive_count':self._archive_count,'archived_events':self._archived_events,
                    'archives':tuple(self._archives),'archives_scope':'recent_archive_names'}

    @staticmethod
    def _verify_archive(path,manifest):
        """Check closed ZIP contents before publishing it or deleting raw data."""
        with zipfile.ZipFile(path,'r') as archive:
            entries=archive.infolist()
            expected={'manifest.json'} | {segment['name'] for segment in manifest['segments']}
            if len(entries)!=len(expected) or {entry.filename for entry in entries}!=expected:
                raise OSError('Archive entries do not match the sealed package')
            if json.loads(archive.read('manifest.json'))!=manifest:
                raise OSError('Archive manifest does not match the sealed package')
            for segment in manifest['segments']:
                if archive.getinfo(segment['name']).file_size!=segment['bytes']:
                    raise OSError('Archived segment size mismatch')
            if archive.testzip() is not None:
                raise OSError('Archive CRC verification failed')

    def _archive_package(self,package_index,segments,event_count,first_sequence,last_sequence):
        paths=[(self.directory/name).resolve() for name in segments]
        # Only this run's closed, directly contained segment files can be removed.
        for path in paths:
            if path.parent!=self.directory or not path.name.startswith(f'trace-{self.run_id}-'):
                raise OSError('Archive source escaped its journal directory')
        manifest={'format_version':1,'run_id':self.run_id,'package_index':package_index,
                  'event_count':event_count,'first_journal_sequence':first_sequence,
                  'last_journal_sequence':last_sequence,
                  'segments':[{'name':path.name,'bytes':path.stat().st_size} for path in paths]}
        name=f'trace-{self.run_id}-package-{package_index:06d}.zip'
        final=self.directory/name
        temporary=self.directory/(name+'.tmp')
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in paths:
                archive.write(path,arcname=path.name)
            archive.writestr('manifest.json',json.dumps(manifest,allow_nan=False,indent=2))
        self._verify_archive(temporary,manifest)
        if final.exists():
            raise FileExistsError('Refusing to replace an existing trace archive')
        temporary.rename(final)
        with self._condition:
            self._archives.append(name)
            self._archive_count+=1
            self._archived_events+=event_count
            self._active_package_events=0
        for path in paths:
            path.unlink()

    def _run(self):
        stream=None
        segment_size=0
        segment_index=0
        package_index=1
        package_segments=[]
        package_events=0
        first_sequence=None
        last_sequence=None
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda:self._queue or self._closing)
                    if not self._queue:
                        break
                    batch=[self._queue.popleft() for _ in range(min(128,len(self._queue)))]
                for line in batch:
                    if stream is None or segment_size+len(line)>self.max_file_bytes:
                        if stream:
                            stream.close()
                        segment_index+=1
                        name=f'trace-{self.run_id}-{segment_index:06d}.jsonl'
                        stream=(self.directory/name).open('xb')
                        segment_size=0
                        with self._condition:
                            self._segments.append(name)
                            self._segment_count+=1
                            if self._first_segment is None:
                                self._first_segment=name
                        package_segments.append(name)
                    stream.write(line)
                    segment_size+=len(line)
                    # Package limits count events; journal sequence may include rejected submissions.
                    last_sequence=json.loads(line)['journal_sequence']
                    if first_sequence is None:
                        first_sequence=last_sequence
                    package_events+=1
                    with self._condition:
                        self._written+=1
                        self._active_package_events=package_events
                    if package_events==self.package_capacity:
                        stream.close()
                        stream=None
                        self._archive_package(package_index,package_segments,package_events,
                                              first_sequence,last_sequence)
                        package_index+=1
                        package_segments=[]
                        package_events=0
                        first_sequence=None
                        with self._condition:
                            self._active_package_events=0
                # Flush each finite batch; a stuck disk never holds an action lock.
                if stream:
                    stream.flush()
        except Exception as error:
            with self._condition:
                self._error=f'{type(error).__name__}: {error}'
                self._queue.clear()
        finally:
            if stream:
                try:
                    stream.close()
                except Exception as error:
                    with self._condition:
                        self._error=f'{type(error).__name__}: {error}'
            with self._condition:
                self._finished=True
            summary=self.stats()
            try:
                (self.directory/f'trace-{self.run_id}-summary.json').write_text(
                    json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
            except Exception as error:
                with self._condition:
                    self._error=f'{type(error).__name__}: {error}'

    def close(self,timeout=2.):
        """Finish accepted events and join the writer within the caller's wait."""
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<0:
            raise ValueError('Close timeout must be finite and nonnegative')
        with self._condition:
            self._closing=True
            self._condition.notify_all()
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()


def iter_trace_events(directory,run_id):
    """Read a run's archived packages and active raw files without duplicates."""
    if not isinstance(run_id,str) or len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):
        raise ValueError('Expected a journal run UUID in hexadecimal form')
    directory=Path(directory).resolve()
    last_archived_segment=0
    for path in sorted(directory.glob(f'trace-{run_id}-package-*.zip')):
        with zipfile.ZipFile(path,'r') as archive:
            manifest=json.loads(archive.read('manifest.json'))
            if manifest['run_id']!=run_id:
                raise ValueError('Archive run identity mismatch')
            for segment in manifest['segments']:
                name=segment['name']
                if Path(name).name!=name or not name.startswith(f'trace-{run_id}-') or not name.endswith('.jsonl'):
                    raise ValueError('Invalid archive segment name')
                with archive.open(name) as stream:
                    for line in stream:
                        yield json.loads(line)
                last_archived_segment=max(last_archived_segment,int(name.rsplit('-',1)[1][:-6]))
    for path in sorted(directory.glob(f'trace-{run_id}-*.jsonl')):
        if int(path.stem.rsplit('-',1)[1])<=last_archived_segment:
            continue
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)
