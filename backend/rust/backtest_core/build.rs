//! 빌드 입력의 지문과 빌드 프로필을 확장 상수 `SOURCE_DIGEST`·`BUILD_PROFILE`로 굽는다.
//!
//! 규칙의 정본은 Python `backtest_engine/engine/core_source.py`다. 이 파일은 같은 규칙을 그대로
//! 옮긴 것이며, 계약 테스트(`tests/test_core_source.py`)가 두 결과가 같음을 고정한다. 수동
//! `maturin develop`로 빌드해도 지문이 들어가므로 엔진이 낡은 확장을 가려낼 수 있다.
//! 어떤 입력 오류에도 panic하지 않는다. 읽지 못한 파일이 있으면 지문을 정본과 맞을 수 없는 값으로
//! 남겨 엔진이 낡은 코어로 판정하게 한다.

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
const SKIPPED_FILES: [&str; 2] = [".DS_Store", "4913"];
const SKIPPED_PREFIXES: [&str; 1] = [".#"];
const SKIPPED_SUFFIXES: [&str; 4] = [".swp", ".swo", ".tmp", "~"];
const UNREADABLE: &str = "unreadable";
const SOURCE_DIRECTORIES: [&str; 2] = ["src", ".cargo"];
// crate 루트에서 지문에 드는 파일과 전부 훑는 디렉터리. 확장 빌드 입력만 본다(정본과 같은 목록).
const ROOT_FILES: [&str; 6] = [
    "build.rs",
    "Cargo.toml",
    "Cargo.lock",
    "pyproject.toml",
    "rust-toolchain",
    "rust-toolchain.toml",
];

const FNV_OFFSET: u128 = 0x6c62272e07bb014262b821756295c58d;
const FNV_PRIME: u128 = 0x0000000001000000000000000000013b;

fn skipped_file(name: &str) -> bool {
    SKIPPED_FILES.contains(&name)
        || SKIPPED_PREFIXES.iter().any(|prefix| name.starts_with(prefix))
        || SKIPPED_SUFFIXES.iter().any(|suffix| name.ends_with(suffix))
}

fn is_symlink(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .map(|meta| meta.file_type().is_symlink())
        .unwrap_or(false)
}

/// 일반 파일만 지문에 넣는다. 링크는 조용히, FIFO·소켓·장치는 경고하고 뺀다(Python과 같은 규칙).
/// 메타데이터를 못 읽으면 실패로 남긴다.
fn regular(path: &Path, inputs: &mut Inputs) -> bool {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_file() => true,
        Ok(meta) => {
            if !meta.file_type().is_symlink() && !meta.is_dir() {
                println!(
                    "cargo:warning=Rust 코어 지문에서 일반 파일이 아닌 항목을 건너뜁니다: {}",
                    path.display()
                );
            }
            false
        }
        Err(_) => {
            inputs.failed = true;
            false
        }
    }
}

/// UTF-8 이름만 받는다. Python 쪽도 같은 이름을 건너뛴다.
fn utf8_name(path: &Path) -> Option<String> {
    let name = path.file_name()?;
    match name.to_str() {
        Some(text) => Some(text.to_owned()),
        None => {
            println!(
                "cargo:warning=Rust 코어 지문에서 UTF-8이 아닌 이름을 건너뜁니다: {}",
                path.display()
            );
            None
        }
    }
}

struct Inputs {
    entries: Vec<(String, PathBuf)>,
    failed: bool,
}

fn key_of(crate_dir: &Path, path: &Path) -> String {
    path.strip_prefix(crate_dir)
        .unwrap_or(path)
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

fn walk(crate_dir: &Path, directory: &Path, inputs: &mut Inputs) {
    // rerun-if-changed를 하나라도 내면 cargo 기본 재실행 규칙이 꺼진다. 디렉터리를 통째로 감시해
    // 새 파일 추가에도 다시 돈다.
    println!("cargo:rerun-if-changed={}", directory.display());
    let Ok(reader) = fs::read_dir(directory) else {
        println!(
            "cargo:warning=Rust 코어 지문 입력 디렉터리를 읽지 못했습니다: {}",
            directory.display()
        );
        inputs.failed = true;
        return;
    };
    let mut children = Vec::new();
    for entry in reader {
        match entry {
            Ok(entry) => children.push(entry.path()),
            Err(_) => inputs.failed = true,
        }
    }
    children.sort();
    for path in children {
        let Some(name) = utf8_name(&path) else {
            continue;
        };
        if is_symlink(&path) {
            continue;
        }
        if path.is_dir() {
            if !SKIPPED_DIRECTORIES.contains(&name.as_str()) {
                walk(crate_dir, &path, inputs);
            }
        } else if !skipped_file(&name) && regular(&path, inputs) {
            inputs.entries.push((key_of(crate_dir, &path), path));
        }
    }
}

// 감시는 존재하는 경로에만 건다. cargo는 없는 경로를 늘 바뀐 것으로 봐 매 빌드 다시 컴파일한다.
// crate 루트 디렉터리는 target/까지 훑어 같은 문제가 있어 감시하지 않는다. 그래서 루트·상위 폴더에
// 후보 파일이나 .cargo/를 새로 만들면 수동 빌드는 이 스크립트를 다시 돌리지 않을 수 있다. 상위
// 디렉터리를 대신 감시하면 저장소 전체를 훑게 되므로 택하지 않았다. `uv run dev`는 빌드 전에
// build.rs 수정 시각을 갱신해 다시 돌리며, 수동 빌드는 build.rs를 touch한 뒤 빌드한다.
fn crate_files(crate_dir: &Path, inputs: &mut Inputs) {
    for name in ROOT_FILES {
        let path = crate_dir.join(name);
        if path.exists() && regular(&path, inputs) {
            println!("cargo:rerun-if-changed={}", path.display());
            inputs.entries.push((name.to_owned(), path));
        }
    }
    for source in SOURCE_DIRECTORIES {
        let root = crate_dir.join(source);
        if root.is_dir() && !is_symlink(&root) {
            walk(crate_dir, &root, inputs);
        }
    }
}

fn ancestors(crate_dir: &Path, inputs: &mut Inputs) {
    let mut directory = crate_dir.parent().map(Path::to_path_buf);
    let mut level = 1;
    while let Some(current) = directory {
        for name in ANCESTOR_FILES {
            let path = current.join(name);
            if path.exists() && regular(&path, inputs) {
                println!("cargo:rerun-if-changed={}", path.display());
                inputs
                    .entries
                    .push((format!("{}{}", "../".repeat(level), name), path));
            }
        }
        if current.join(".git").exists() {
            break;
        }
        directory = current.parent().map(Path::to_path_buf);
        level += 1;
    }
}

fn digest(inputs: &mut Inputs) -> String {
    inputs
        .entries
        .sort_by(|left, right| left.0.as_bytes().cmp(right.0.as_bytes()));
    let mut value = FNV_OFFSET;
    for (key, path) in &inputs.entries {
        let Ok(content) = fs::read(path) else {
            inputs.failed = true;
            continue;
        };
        for chunk in [key.as_bytes(), b"\0", content.as_slice(), b"\0"] {
            for byte in chunk {
                value ^= u128::from(*byte);
                value = value.wrapping_mul(FNV_PRIME);
            }
        }
    }
    if inputs.failed {
        println!("cargo:warning=Rust 코어 지문 입력 일부를 읽지 못해 엔진이 이 빌드를 낡은 것으로 봅니다");
        return UNREADABLE.to_owned();
    }
    format!("{value:032x}")
}

fn main() {
    let Ok(manifest) = env::var("CARGO_MANIFEST_DIR") else {
        println!("cargo:rustc-env=BACKTEST_CORE_SOURCE_DIGEST={UNREADABLE}");
        println!("cargo:rustc-env=BACKTEST_CORE_BUILD_PROFILE=unknown");
        return;
    };
    let crate_dir = PathBuf::from(manifest);
    let mut inputs = Inputs {
        entries: Vec::new(),
        failed: false,
    };
    crate_files(&crate_dir, &mut inputs);
    ancestors(&crate_dir, &mut inputs);
    let value = digest(&mut inputs);
    println!("cargo:rustc-env=BACKTEST_CORE_SOURCE_DIGEST={value}");
    let profile = env::var("PROFILE").unwrap_or_else(|_| "unknown".to_owned());
    println!("cargo:rustc-env=BACKTEST_CORE_BUILD_PROFILE={profile}");
}
