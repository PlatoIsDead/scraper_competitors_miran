import pandas as pd

PROVIDERS = [
    "miran", "selectel", "1dedic", "regcloud",
    "hostkey", "it-lite", "netrack", "timeweb",
]


def add_readable_labels(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cpu = df["cpu_model_norm"].str.title()
    has_gen = df["cpu_generation"].notna() & (df["cpu_generation"] != "")
    cpu[has_gen] += " (" + df.loc[has_gen, "cpu_generation"] + ")"
    df["CPU"] = cpu
    df["RAM"] = df["ram_gb"].astype(str) + " ГБ"
    df["Диск"] = (
        df["disk_count"].astype(str) + " × " +
        df["disk_size_gb"].astype(str) + " ГБ " +
        df["disk_type"]
    )
    return df


def build_pivot(df: pd.DataFrame, providers: list[str] = PROVIDERS) -> pd.DataFrame:
    """Pivot configs × providers with min price per cell."""
    df = add_readable_labels(df)
    pivot = df.pivot_table(
        index=["CPU", "RAM", "Диск"],
        columns="provider",
        values="price_rub",
        aggfunc="min",
    ).reset_index()
    for p in providers:
        if p not in pivot.columns:
            pivot[p] = float("nan")
    return pivot
