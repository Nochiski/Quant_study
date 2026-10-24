//! 빌드 입력의 지문을 계산해 확장 상수 `SOURCE_DIGEST`로 굽는다.
//!
//! 규칙의 정본은 Python `backtest_engine/engine/core_source.py`다. 이 파일은 같은 규칙을 그대로
//! 옮긴 것이며, 계약 테스트(`tests/test_core_source.py`)가 두 결과가 같음을 고정한다. 수동
//! `maturin develop`로 빌드해도 지문이 들어가므로 엔진이 낡은 확장을 가려낼 수 있다.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const ANCESTOR_FILES: [&str; 4] = [
    "rust-toolchain",
    "rust-toolchain.toml",
    ".cargo/config",
    ".cargo/config.toml",
];
const SKIPPED_DIRECTORIES: [&str; 2] = [".idea", ".vscode"];
const SKIPPED_FILES: [&str; 1] = [".DS_Store"];
const SKIPPED_SUFFIXES: [&str; 3] = [".swp", ".swo", "~"];

const FNV_OFFSET: u128 = 0x6c62272e07bb014262b821756295c58d;
const FNV_PRIME: u128 = 0x0000000001000000000000000000013b;

fn skipped_file(name: &str) -> bool {
    SKIPPED_FILES.contains(&name) || SKIPPED_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
}

fn posix_key(relative: &Path) -> String {
    relative
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

fn walk(crate_dir: &Path, directory: &Path, entries: &mut Vec<(String, PathBuf)>) {
    let mut children: Vec<_> = fs::read_dir(directory)
        .expect("crate 디렉터리를 읽지 못했다")
        .map(|entry| entry.expect("디렉터리 항목을 읽지 못했다").path())
        .collect();
    children.sort();
    for path in children {
        let name = path.file_name().unwrap().to_string_lossy().into_owned();
        if path.is_dir() {
            // 빌드 산출물은 crate 최상위 target/ 하나다.
            let top_target = directory == crate_dir && name == "target";
            if !top_target && !SKIPPED_DIRECTORIES.contains(&name.as_str()) {
                // 새 파일이 생겨도 다시 계산하게 디렉터리를 감시한다.
                println!("cargo:rerun-if-changed={}", path.display());
                walk(crate_dir, &path, entries);
            }
        } else if !skipped_file(&name) {
            let key = posix_key(path.strip_prefix(crate_dir).unwrap());
            entries.push((key, path));
        }
    }
}

fn ancestors(crate_dir: &Path, entries: &mut Vec<(String, PathBuf)>) {
    let mut directory = crate_dir.parent().map(Path::to_path_buf);
    let mut level = 1;
    while let Some(current) = directory {
        for name in ANCESTOR_FILES {
            let path = current.join(name);
            if path.is_file() {
                entries.push((format!("{}{}", "../".repeat(level), name), path));
            }
        }
        if current.join(".git").exists() {
            break;
        }
        directory = current.parent().map(Path::to_path_buf);
        level += 1;
    }
}

fn main() {
    let crate_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
    let mut entries = Vec::new();
    walk(&crate_dir, &crate_dir, &mut entries);
    ancestors(&crate_dir, &mut entries);
    entries.sort_by(|left, right| left.0.as_bytes().cmp(right.0.as_bytes()));
    let mut value = FNV_OFFSET;
    for (key, path) in &entries {
        println!("cargo:rerun-if-changed={}", path.display());
        let content = fs::read(path).expect("지문 입력 파일을 읽지 못했다");
        for chunk in [key.as_bytes(), b"\0", content.as_slice(), b"\0"] {
            for byte in chunk {
                value ^= u128::from(*byte);
                value = value.wrapping_mul(FNV_PRIME);
            }
        }
    }
    println!("cargo:rustc-env=BACKTEST_CORE_SOURCE_DIGEST={value:032x}");
}
