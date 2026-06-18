project(
    'turbohtml',
    'c',
    version: run_command(['tools/generate_version.py'], check: true).stdout().strip(),
    meson_version: '>=1.5.0',
    default_options: ['c_std=c11', 'warning_level=2'],
)

# Freeze the git-derived version into the sdist so wheels built from it need no git history.
meson.add_dist_script('tools/generate_version.py', '--write', 'src/turbohtml/_version.py', '--meson-dist')

# Keep the development-only submodule corpora out of the sdist regardless of local checkout state.
meson.add_dist_script('tools/prune_dist.py')

py = import('python').find_installation(pure: false)

cc = meson.get_compiler('c')
incdir = include_directories('src/turbohtml')

# The 1-byte escape clean-run scan widens from 16 to 32 bytes per step on x86
# CPUs that report AVX2 at runtime. The AVX2 intrinsics need -mavx2, which would
# crash pre-AVX2 CPUs if applied module-wide, so the AVX2 scan lives in its own
# translation unit built with -mavx2 and is dispatched at runtime by escape.c.
# It is compiled only on x86 toolchains that accept the flag; every other build
# (ARM NEON, SWAR, MSVC) keeps the existing SSE2/NEON/SWAR path untouched.
module_args = []
extra_link = []
if host_machine.cpu_family() in ['x86', 'x86_64'] and cc.has_argument('-mavx2')
    escape_avx2 = static_library(
        'turbohtml_escape_avx2',
        'src/turbohtml/escape_avx2.c',
        include_directories: incdir,
        dependencies: py.dependency(),
        c_args: ['-mavx2'],
        install: false,
    )
    module_args += '-DTURBOHTML_HAVE_AVX2'
    extra_link += escape_avx2
endif

py.extension_module(
    '_html',
    [
        'src/turbohtml/_htmlmodule.c',
        'src/turbohtml/charref.c',
        'src/turbohtml/escape.c',
        'src/turbohtml/linkify.c',
        'src/turbohtml/markup.c',
        'src/turbohtml/sanitize.c',
        'src/turbohtml/token_type.c',
        'src/turbohtml/tokenizer_sm.c',
        'src/turbohtml/tokenizer_type.c',
        'src/turbohtml/treebuilder.c',
        'src/turbohtml/treebuilder_py.c',
        'src/turbohtml/tree_type.c',
        'src/turbohtml/unescape.c',
    ],
    c_args: module_args,
    link_with: extra_link,
    include_directories: incdir,
    install: true,
    subdir: 'turbohtml',
)

py.install_sources(
    [
        'src/turbohtml/__init__.py',
        'src/turbohtml/_html.pyi',
        'src/turbohtml/bleach_compat.py',
        'src/turbohtml/linkify.py',
        'src/turbohtml/markup.py',
        'src/turbohtml/sanitizer.py',
        'src/turbohtml/py.typed',
    ],
    subdir: 'turbohtml',
)
