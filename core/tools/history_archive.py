"""Explicit offline archive/export commands; never loaded as a model tool."""
from pathlib import Path
import argparse
import json
import shutil
from tempfile import TemporaryDirectory
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from telyincore.source_bytes import Generation,checked_open,digest_file
from telyincore.history_archive import build,export,ArchiveReader


def measure_copy(source, *, namespace, logical_path, kind, codec, block_size):
    """Measure the complete archive on a disposable, byte-verified copy."""
    identity, digest = digest_file(source)
    generation = Generation(namespace, logical_path, digest, identity[2])
    with TemporaryDirectory(prefix='core-history-measure-') as directory:
        root = Path(directory)
        copy = root / 'copy'
        with checked_open(source, expected=identity) as original, copy.open('xb') as target:
            shutil.copyfileobj(original, target, length=65536)
        grant = lambda item: item == generation
        archive = root / 'archive'
        result = build(copy, archive, generation, grant, kind=kind, codec=codec, block_size=block_size)
        # Export revalidates all blocks and the digest of the reconstructed file.
        export(ArchiveReader(archive, generation, grant, kind=kind), root / 'restored')
        packed = result['archive_bytes']
        return {'original_bytes': generation.size, 'archive_bytes': packed,
                'saving_if_smaller': max(0, generation.size - packed),
                'kind': kind, 'codec': codec, 'round_trip_verified': True}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline lossless archive; originals remain in place')
    parser.add_argument('action',choices=['build','export','measure'])
    parser.add_argument('--source',type=Path);parser.add_argument('--archive',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--namespace',required=True);parser.add_argument('--logical-path',required=True)
    parser.add_argument('--sha256');parser.add_argument('--size',type=int)
    parser.add_argument('--kind',choices=['sqlite','seekable'],default='sqlite')
    parser.add_argument('--codec',choices=['gzip','zstd'],default='gzip')
    parser.add_argument('--block-size',type=int,default=262144)
    parser.add_argument('--only-if-smaller', action='store_true',
                        help='publish a verified build only if all archive files together are smaller than the source')
    args=parser.parse_args(argv)
    if args.only_if_smaller and args.action != 'build':
        parser.error('--only-if-smaller applies to build')
    # Explicit local-admin invocation only; no network/model input is routed here.
    grant=lambda generation: generation.namespace==args.namespace and generation.logical_path==args.logical_path
    if args.action != 'measure' and args.output is None:parser.error('build/export require --output')
    if args.action=='measure':
        if args.source is None or args.output is not None:parser.error('measure requires --source and manages its own temporary output')
        result=measure_copy(args.source, namespace=args.namespace, logical_path=args.logical_path,
                            kind=args.kind, codec=args.codec, block_size=args.block_size)
    elif args.action=='build':
        if args.source is None:parser.error('build requires --source')
        identity,digest=digest_file(args.source)
        generation=Generation(args.namespace,args.logical_path,digest,identity[2])
        result=build(args.source,args.output,generation,grant,kind=args.kind,codec=args.codec,block_size=args.block_size,only_if_smaller=args.only_if_smaller)
    else:
        if args.archive is None or args.sha256 is None or args.size is None:parser.error('export requires --archive, --sha256 and --size')
        generation=Generation(args.namespace,args.logical_path,args.sha256,args.size)
        reader=ArchiveReader(args.archive,generation,grant,kind=args.kind)
        export(reader,args.output);result={'exported':str(args.output),'sha256':generation.sha256,'bytes':generation.size}
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
