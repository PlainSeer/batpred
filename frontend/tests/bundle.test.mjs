import test from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

test('bundling is reproducible and includes every build file with the serving prefix', () => {
  const script = fileURLToPath(new URL('../scripts/bundle.py', import.meta.url))
  const result = spawnSync('python3', ['-c', `
import importlib.util, pathlib, tempfile, zipfile, os
spec = importlib.util.spec_from_file_location('bundle', ${JSON.stringify(script)})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    dist = root / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<div id="root"></div>')
    asset = dist / 'assets' / 'example.js'
    asset.write_bytes(b'export const value = 1;')
    module.build_bundle(dist, root / 'first.zip')
    os.utime(asset, (1700000000, 1700000000))
    module.build_bundle(dist, root / 'second.zip')
    assert (root / 'first.zip').read_bytes() == (root / 'second.zip').read_bytes()
    with zipfile.ZipFile(root / 'first.zip') as archive:
        assert archive.namelist() == ['dist/assets/example.js', 'dist/index.html']
        assert archive.read('dist/assets/example.js') == asset.read_bytes()
        assert archive.testzip() is None
    (dist / 'index.html').unlink()
    try:
        module.build_bundle(dist, root / 'missing.zip')
    except FileNotFoundError:
        pass
    else:
        raise AssertionError('Missing build must fail')
`], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stdout + result.stderr)
})
