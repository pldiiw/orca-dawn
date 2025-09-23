import argparse
import os
import sys
import platform
import shutil
import subprocess
from contextlib import contextmanager

@contextmanager
def pushd(new_dir):
    previous_dir = os.getcwd()
    os.chdir(new_dir)
    try:
        yield
    finally:
        os.chdir(previous_dir)

def fixup_line_endings(file_path):
    file_data = ""

    with open(file_path, 'rb') as file:
        file_data = file.read()
        
    file_data = file_data.replace(b'\r\n',  b'\n')

    with open(file_path, 'wb') as file:
        file.write(file_data)


##### main:

parser = argparse.ArgumentParser()
parser.add_argument("--config", default="release", help="compile in debug config (default is release)")
parser.add_argument("--parallel", type=int)
argv = sys.argv[1:]
args = parser.parse_args(argv)

DAWN_COMMIT = ""
with open("commit.txt", "r") as f:
    DAWN_COMMIT = f.read().strip()

if DAWN_COMMIT == "":
    print("Failed to find commit.txt")
    exit(1)

input_config = args.config
config = input_config.lower()
if config != "release" and config != "debug":
    print("Invalid configuration " + config + ", only 'release' or 'debug' allowed")
    exit(1)

print("Building dawm at sha " + DAWN_COMMIT + " in config: " + config)

os.makedirs("build", exist_ok=True)

with pushd("build"):
    # get depot tools repo
    print("  * checking depot tools")
    if not os.path.exists("depot_tools"):
        subprocess.run([
            "git", "clone", "--depth=1", "--no-tags", "--single-branch",
            "https://chromium.googlesource.com/chromium/tools/depot_tools.git"
        ], check=True)
    os.environ["PATH"] = os.path.join(os.getcwd(), "depot_tools") + os.pathsep + os.environ["PATH"]
    os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"

    # get dawn repo
    print("  * checking dawn")
    if not os.path.exists("dawn"):
        subprocess.run([
            "git", "clone", "--no-tags", "--single-branch",
            "https://dawn.googlesource.com/dawn"
        ], check=True)

    with pushd("dawn"):
        subprocess.run([
            "git", "restore", "."
        ], check=True)

        subprocess.run([
            "git", "pull", "--force", "--no-tags"
        ], check=True)

        subprocess.run([
            "git", "checkout", "--force", DAWN_COMMIT
        ], check=True)

        shutil.copy("scripts/standalone.gclient", ".gclient")

        shell = True if platform.system() == "Windows" else False
        subprocess.run(["gclient", "sync"], shell=shell, check=True)

    print("  * preparing build")

    with open("dawn/src/dawn/native/CMakeLists.txt", "a") as f:
        s = """add_library(webgpu SHARED ${DAWN_PLACEHOLDER_FILE})
common_compile_options(webgpu)
target_link_libraries(webgpu PRIVATE dawn_native)
target_link_libraries(webgpu PUBLIC dawn_headers)
target_compile_definitions(webgpu PRIVATE WGPU_IMPLEMENTATION WGPU_SHARED_LIBRARY)
target_sources(webgpu PRIVATE ${WEBGPU_DAWN_NATIVE_PROC_GEN})"""
        f.write(s)

    # apply windows patch
    with pushd("dawn"):
        subprocess.run(["git", "apply", "-v", "../../patch/dawn-d3d12-transparent.diff"], check=True)

    if platform.system() == "Windows":
        backends = [
            "-D", "DAWN_ENABLE_D3D12=ON",
            "-D", "DAWN_ENABLE_D3D11=OFF",
            "-D", "DAWN_ENABLE_METAL=OFF",
            "-D", "DAWN_ENABLE_NULL=OFF",
            "-D", "DAWN_ENABLE_DESKTOP_GL=OFF",
            "-D", "DAWN_ENABLE_OPENGLES=OFF",
            "-D", "DAWN_ENABLE_VULKAN=OFF"
        ]
    elif platform.system() == "Darwin":
        backends = [
            "-D", "DAWN_ENABLE_METAL=ON",
            "-D", "DAWN_ENABLE_NULL=OFF",
            "-D", "DAWN_ENABLE_DESKTOP_GL=OFF",
            "-D", "DAWN_ENABLE_OPENGLES=OFF",
            "-D", "DAWN_ENABLE_VULKAN=OFF"
        ]
    elif platform.system() == "Linux":
        backends = [
            "-D", "DAWN_ENABLE_NULL=OFF",
            "-D", "DAWN_ENABLE_DESKTOP_GL=OFF",
            "-D", "DAWN_ENABLE_OPENGLES=OFF",
            "-D", "DAWN_ENABLE_VULKAN=ON",
            "-D", "DAWN_USE_WAYLAND=ON",
            "-D", "DAWN_USE_X11=ON"
        ]
    else:
        assert False, "Unsupported OS"

    subprocess.run([
        "cmake",
        "-S", "dawn",
        "-B", "dawn.build",
        "-D", f"CMAKE_BUILD_TYPE={config}",
        "-D", "CMAKE_POLICY_DEFAULT_CMP0091=NEW",
        "-D", "BUILD_SHARED_LIBS=OFF",
        "-D", "BUILD_SAMPLES=ON",
        *backends,
        "-D", "DAWN_BUILD_SAMPLES=ON",
        "-D", "TINT_BUILD_SAMPLES=OFF",
        "-D", "TINT_BUILD_DOCS=OFF",
        "-D", "TINT_BUILD_TESTS=OFF"
    ], check = True)

    parallel = ["--parallel"]
    if args.parallel != None:
        parallel.append(str(args.parallel))

    print("  * building")
    subprocess.run([
        "cmake", "--build", "dawn.build", "--config", config, "--target", "webgpu", *parallel
    ], check = True)

    # on macOS, change install name to executable path
    if platform.system() == "Darwin":
        subprocess.run(['install_name_tool', '-id', '@executable_path/libwebgpu.dylib', 'dawn.build/src/dawn/native/libwebgpu.dylib'], check=True)

    # package result
    print("  * copying build artifacts...")

    os.makedirs("dawn.out/include", exist_ok=True)
    os.makedirs("dawn.out/lib", exist_ok=True)

    shutil.copy("dawn.build/gen/include/dawn/webgpu.h", "dawn.out/include/")

    if platform.system() == "Windows":
        shutil.copy(f"dawn.build/{config}/webgpu.dll", "dawn.out/lib/")
        shutil.copy(f"dawn.build/src/dawn/native/{config}/webgpu.lib", "dawn.out/lib/")
    elif platform.system() == "Darwin":
        shutil.copy("dawn.build/src/dawn/native/libwebgpu.dylib", "dawn.out/lib/")
    elif platform.system() == "Linux":
        shutil.copy("dawn.build/src/dawn/native/libwebgpu.so", "dawn.out/lib/")
    else:
        assert False, "Unsupported OS"

    # ensure line endings are consistent between windows/unix systems since they
    # will be used in the Orca project
    fixup_line_endings("dawn.out/include/webgpu.h")
