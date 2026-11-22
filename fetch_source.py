"""Fetch only the contest-pinned public sqlglot source during image build."""
import io
from pathlib import Path, PurePosixPath
import tarfile
from urllib.request import urlopen

PIN='eb11aec4be8cb2122afe713a4816662c3f0fb2fa'
URL='https://codeload.github.com/tobymao/sqlglot/tar.gz/'+PIN
root=Path('/opt/sqlglot');root.mkdir(parents=True,exist_ok=True)
with urlopen(URL,timeout=120) as response:data=response.read()
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
    for member in archive.getmembers():
        parts=PurePosixPath(member.name).parts
        if len(parts)<2:continue
        if not parts[0].endswith(PIN):raise ValueError('Unexpected source archive')
        member.name=str(PurePosixPath(*parts[1:]))
        if '..' in PurePosixPath(member.name).parts:raise ValueError('Unsafe source path')
        archive.extract(member,root,filter='data')
