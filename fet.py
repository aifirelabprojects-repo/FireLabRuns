from pathlib import Path

DEFAULT_DATA_FOLDER = Path("./data")

def folder_stats(folder: Path):
    if not folder.exists() or not folder.is_dir():
        raise ValueError(f"{folder} is not a valid folder path")

    total_size = 0
    file_count = 0

    for file in folder.rglob('*'):  # recursively traverse
        if file.is_file():
            file_count += 1
            total_size += file.stat().st_size

    def sizeof_fmt(num, suffix="B"):
        for unit in ["", "K", "M", "G", "T"]:
            if num < 1024:
                return f"{num:.2f} {unit}{suffix}"
            num /= 1024
        return f"{num:.2f} P{suffix}"

    return {
        "file_count": file_count,
        "total_size_bytes": total_size,
        "total_size_readable": sizeof_fmt(total_size)
    }

if __name__ == "__main__":
    stats = folder_stats(DEFAULT_DATA_FOLDER)
    print(f"📦 Files: {stats['file_count']}")
    print(f"💾 Total Size: {stats['total_size_readable']}")
