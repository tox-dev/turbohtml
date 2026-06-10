project(
    'turbohtml',
    'c',
    version: run_command(['tools/generate_version.py'], check: true).stdout().strip(),
    meson_version: '>=1.5.0',
    default_options: ['c_std=c11', 'warning_level=2'],
)

# Freeze the git-derived version into the sdist so wheels built from it need no git history.
meson.add_dist_script('tools/generate_version.py', '--write', 'src/turbohtml/_version.py', '--meson-dist')

py = import('python').find_installation(pure: false)

py.extension_module(
    '_html',
    [
        'src/turbohtml/_htmlmodule.c',
        'src/turbohtml/charref.c',
        'src/turbohtml/escape.c',
        'src/turbohtml/token_type.c',
        'src/turbohtml/tokenizer_sm.c',
        'src/turbohtml/tokenizer_type.c',
        'src/turbohtml/unescape.c',
    ],
    include_directories: include_directories('src/turbohtml'),
    install: true,
    subdir: 'turbohtml',
)

py.install_sources(
    [
        'src/turbohtml/__init__.py',
        'src/turbohtml/_html.pyi',
        'src/turbohtml/py.typed',
    ],
    subdir: 'turbohtml',
)
