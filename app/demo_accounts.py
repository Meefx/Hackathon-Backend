"""Deterministic fictional portfolio data for the explicit demo seed."""

FIRST_NAMES = (
    "Aditya", "Agus", "Andi", "Arief", "Bagas", "Bambang", "Bayu", "Bima",
    "Dedi", "Dimas", "Eko", "Fajar", "Farhan", "Hadi", "Ilham", "Rizky",
    "Anisa", "Ayu", "Citra", "Dewi", "Dinda", "Fitri", "Intan", "Kartika",
    "Laras", "Maya", "Nadia", "Nisa", "Putri", "Ratna", "Sari", "Wulan",
)
LAST_NAMES = (
    "Pratama", "Saputra", "Wijaya", "Santoso", "Nugroho", "Setiawan", "Hidayat", "Kusuma",
    "Wibowo", "Ramadhan", "Maulana", "Firmansyah", "Prasetyo", "Hakim", "Permana", "Kurniawan",
    "Pangestu", "Utama", "Darmawan", "Mahendra", "Anggara", "Satria", "Suryana", "Rahman",
    "Gunawan", "Herlambang", "Purnama", "Sudirman", "Susanto", "Yulianto", "Hartono", "Iskandar",
)
COLLECTORS = (
    "Sarah Wilson", "Daniel Brooks", "Rina Maharani",
    "Budi Hermawan", "Sinta Lestari", "Arif Budiman",
)
DAYS_PAST_DUE = (
    -30, -21, -14, -7, 0, 1, 3, 5, 7, 10, 14, 21, 30,
    31, 45, 60, 75, 90, 91, 105, 120, 150, 180,
)


def demo_accounts(count: int) -> list[tuple[str, str, int, int, str]]:
    if count < 1:
        raise ValueError("Demo account count must be positive")

    samples = [
        ("CF-1001", "Alex Morgan", 4_500_000, 104, "Sarah Wilson"),
        ("CF-1002", "Jordan Lee", 2_750_000, 67, "Daniel Brooks"),
        ("CF-1003", "Taylor Reed", 1_200_000, 42, "Sarah Wilson"),
        ("CF-1004", "Casey Parker", 3_600_000, 24, "Daniel Brooks"),
        ("CF-1005", "Riley Quinn", 850_000, 12, "Sarah Wilson"),
        ("CF-1006", "Jamie Blake", 5_000_000, 5, "Daniel Brooks"),
        ("CF-1007", "Avery Stone", 2_100_000, 0, "Sarah Wilson"),
        ("CF-1008", "Morgan Ellis", 1_500_000, -7, "Daniel Brooks"),
    ][:count]
    for index in range(count - len(samples)):
        first_name = FIRST_NAMES[index % len(FIRST_NAMES)]
        last_name = LAST_NAMES[(index // len(FIRST_NAMES)) % len(LAST_NAMES)]
        samples.append((
            f"CF-{1009 + index}",
            f"{first_name} {last_name}",
            250_000 + (index * 73 % 790) * 25_000,
            DAYS_PAST_DUE[index % len(DAYS_PAST_DUE)],
            COLLECTORS[index % len(COLLECTORS)],
        ))
    return samples
