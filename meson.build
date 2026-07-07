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

# Strip the shipped object's local .symtab at link time. The release wheels pass -Dstrip=true, but meson-python
# packages straight from the build directory and never runs the install-time strip that option drives, so we do
# it in the link instead. extension_module already defaults to -fvisibility=hidden (only PyInit__html stays
# dynamic) and -Db_lto has dropped the unreachable internals, so .symtab is the last slack: ~65 KB off the Linux
# .so, ~46 KB off the macOS bundle. Gating on -Dstrip keeps every local symbol in the coverage, sanitizer, tsan,
# codspeed, and warnings builds, which never set it, for gdb/gcov/valgrind/CodSpeed. The flag is applied
# directly, not via get_supported_link_arguments: ld64 warns on -Wl,-x and meson's probe misreads the warning as
# unsupported. -Dstrip fires only on the cibuildwheel toolchains (GNU ld/lld, ld64, MSVC), so the branch is safe.
release_link_args = []
if get_option('strip')
    strip_system = host_machine.system()
    if strip_system == 'darwin'
        release_link_args = ['-Wl,-x']
    elif strip_system != 'windows'  # MSVC's release .pyd carries no symbol table to strip
        release_link_args = ['-Wl,--strip-all']
    endif
endif

py.extension_module(
    '_html',
    [
        'src/turbohtml/_c/core/module.c',
        'src/turbohtml/_c/tokenizer/charref.c',
        'src/turbohtml/_c/tokenizer/statemachine.c',
        'src/turbohtml/_c/tokenizer/token.c',
        'src/turbohtml/_c/tokenizer/tokenizer.c',
        'src/turbohtml/_c/dom/tree.c',
        'src/turbohtml/_c/dom/mutate.c',
        'src/turbohtml/_c/dom/binding.c',
        'src/turbohtml/_c/dom/node.c',
        'src/turbohtml/_c/dom/element.c',
        'src/turbohtml/_c/dom/leaf.c',
        'src/turbohtml/_c/dom/traversal.c',
        'src/turbohtml/_c/dom/formatters.c',
        'src/turbohtml/_c/dom/document.c',
        'src/turbohtml/_c/serialize/escape.c',
        'src/turbohtml/_c/serialize/unescape.c',
        'src/turbohtml/_c/serialize/markup.c',
        'src/turbohtml/_c/serialize/document.c',
        'src/turbohtml/_c/serialize/minify.c',
        'src/turbohtml/_c/serialize/css.c',
        'src/turbohtml/_c/serialize/markdown.c',
        'src/turbohtml/_c/serialize/text.c',
        'src/turbohtml/_c/serialize/readability.c',
        'src/turbohtml/_c/serialize/js/lexer.c',
        'src/turbohtml/_c/serialize/js/ast.c',
        'src/turbohtml/_c/serialize/js/parser.c',
        'src/turbohtml/_c/serialize/js/printer.c',
        'src/turbohtml/_c/serialize/js/fold.c',
        'src/turbohtml/_c/serialize/js/mangle.c',
        'src/turbohtml/_c/serialize/js/minify.c',
        'src/turbohtml/_c/serialize/js/lexdump.c',
        'src/turbohtml/_c/query/css/selector.c',
        'src/turbohtml/_c/query/css/to_xpath.c',
        'src/turbohtml/_c/query/methods.c',
        'src/turbohtml/_c/query/xpath/ast.c',
        'src/turbohtml/_c/query/xpath/lexer.c',
        'src/turbohtml/_c/query/xpath/parser.c',
        'src/turbohtml/_c/query/xpath/eval.c',
        'src/turbohtml/_c/query/xpath/functions.c',
        'src/turbohtml/_c/query/find/find.c',
        'src/turbohtml/_c/features/sanitize.c',
        'src/turbohtml/_c/features/linkify.c',
        'src/turbohtml/_c/features/links.c',
        'src/turbohtml/_c/url/url.c',
        'src/turbohtml/_c/url/idna.c',
        'src/turbohtml/_c/unicode/normalize.c',
        'src/turbohtml/_c/url/registrable.c',
        'src/turbohtml/_c/features/annotation.c',
        'src/turbohtml/_c/features/structured_data.c',
        'src/turbohtml/_c/features/feed.c',
        'src/turbohtml/_c/features/tables.c',
        'src/turbohtml/_c/features/dates.c',
    ],
    include_directories: include_directories('src/turbohtml/_c'),
    link_args: release_link_args,
    install: true,
    subdir: 'turbohtml',
)

py.install_sources(
    [
        'src/turbohtml/__init__.py',
        'src/turbohtml/__main__.py',
        'src/turbohtml/_article.py',
        'src/turbohtml/_cssmin.py',
        'src/turbohtml/_dates.py',
        'src/turbohtml/_feed.py',
        'src/turbohtml/_html.pyi',
        'src/turbohtml/_jsminify.py',
        'src/turbohtml/_linkify.py',
        'src/turbohtml/_links.py',
        'src/turbohtml/_locations.py',
        'src/turbohtml/_match.py',
        'src/turbohtml/_minify.py',
        'src/turbohtml/_render.py',
        'src/turbohtml/_sanitizer.py',
        'src/turbohtml/_selectors.py',
        'src/turbohtml/_structured_data.py',
        'src/turbohtml/_urls.py',
        'src/turbohtml/_xpath.py',
        'src/turbohtml/build.py',
        'src/turbohtml/clean.py',
        'src/turbohtml/convert.py',
        'src/turbohtml/detect.py',
        'src/turbohtml/extract.py',
        'src/turbohtml/query.py',
        'src/turbohtml/traverse.py',
        'src/turbohtml/py.typed',
    ],
    subdir: 'turbohtml',
)

py.install_sources(
    [
        'src/turbohtml/_stubs/__init__.pyi',
        'src/turbohtml/_stubs/dom.pyi',
        'src/turbohtml/_stubs/features.pyi',
        'src/turbohtml/_stubs/query.pyi',
        'src/turbohtml/_stubs/serialize.pyi',
        'src/turbohtml/_stubs/tokenizer.pyi',
    ],
    subdir: 'turbohtml/_stubs',
)

py.install_sources(
    [
        'src/turbohtml/migration/__init__.py',
        'src/turbohtml/migration/bleach.py',
        'src/turbohtml/migration/markupsafe.py',
        'src/turbohtml/migration/stdlib.py',
    ],
    subdir: 'turbohtml/migration',
)
