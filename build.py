
from enum import StrEnum, auto
from pathlib import Path
import subprocess

from pycpkg import FileSet, extract, download, cmake_build, autotools_build, cmake_install, cache
from pycpkg.cmake import CMakeBuildType
from pycpkg.core import cache

LLVM_VERSION = '19.1.7'
ICU_VERSION = '76.1'

here = Path(__file__).parent.resolve()

class LLVMTarget(StrEnum):
    host = auto()
    AArch64 = auto()
    AMDGPU = auto()
    ARC = auto()
    ARM = auto()
    AVR = auto()
    BPF = auto()
    CSKY = auto()
    DirectX = auto()
    Hexagon = auto()
    Lanai = auto()
    LoongArch = auto()
    M68k = auto()
    MSP430 = auto()
    Mips = auto()
    NVPTX = auto()
    PowerPC = auto()
    RISCV = auto()
    SPIRV = auto()
    Sparc = auto()
    SystemZ = auto()
    VE = auto()
    WebAssembly = auto()
    X86 = auto()
    XCore = auto()
    Xtensa = auto()

@cache
def llvm(
    version: str,
    assertions: bool = False,
    build_type: CMakeBuildType = CMakeBuildType.release,
    targets: list[LLVMTarget] | None = None,
) -> FileSet:
    sources = extract(download(f'https://github.com/llvm/llvm-project/archive/refs/tags/llvmorg-{version}.tar.gz'), strip_components=1)
    if targets is None:
        targets = [ LLVMTarget.host ]
    return cmake_install(
        cmake_build(
            source_dir=sources,
            build_type=build_type,
            defines={
                'LLVM_ENABLE_ASSERTIONS': assertions,
                'LLVM_TARGETS_TO_BUILD': ';'.join(targets),
            }
        )
    )

@cache
def icu(version: str) -> FileSet:
    version_alt = version.replace('.', '-')
    sources = extract(download(f'https://github.com/unicode-org/icu/releases/download/release-{version_alt}/icu4c-{version_alt}-src.tgz'), strip_components=1)
    return autotools_build(sources)

@cache
def bolt(
    build_type: CMakeBuildType = CMakeBuildType.release
) -> FileSet:
    llvm_binaries = llvm(LLVM_VERSION)
    icu_binaries = icu(ICU_VERSION)
    sources = FileSet.from_dir(here)
    return cmake_install(
        cmake_build (
            sources,
            build_type=build_type,
            defines={
                'LLVM_ROOT': llvm_binaries.path,
                'ICU_ROOT': icu_binaries.path,
                'EXPORT_COMPILE_COMMANDS': True,
                'ZEN_ENABLE_TESTS': False,
                'BOLT_ENABLE_TESTS': True,
            },
        )
    )

def test() -> None:
    binaries = bolt(build_type=CMakeBuildType.debug)
    subprocess.run(binaries.pat / 'alltests', check=True)
