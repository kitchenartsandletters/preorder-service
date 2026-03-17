import pandas as pd

def compare_csvs(file_old, file_new, key_column):
    # Load the datasets
    df_old = pd.read_csv(file_old)
    df_new = pd.read_csv(file_new)

    df_old.set_index(key_column, inplace=True)
    df_new.set_index(key_column, inplace=True)

    # 1. Unique to OLD (Deleted in New)
    dropped_idx = df_old.index.difference(df_new.index)
    
    # 2. Unique to NEW (Added)
    added_idx = df_new.index.difference(df_old.index)

    # 3. Intersection (Rows in both)
    common_idx = df_old.index.intersection(df_new.index)
    
    # 4. Of the common rows, which are identical?
    # We compare the DataFrames only where the index matches
    matches_mask = (df_old.loc[common_idx] == df_new.loc[common_idx]).all(axis=1)
    identical_count = matches_mask.sum()
    
    # 5. Of the common rows, which changed?
    modified_idx = common_idx[~matches_mask]

    print(f"\n--- Comparison Summary ---")
    print(f"✅ Identical Matches: {identical_count}")
    print(f"❌ Deleted (In Old only): {len(dropped_idx)}")
    print(f"✨ Added (In New only):   {len(added_idx)}")
    print(f"📝 Modified:             {len(modified_idx)}")

    if not dropped_idx.empty:
        print(f"\n--- ROWS UNIQUE TO OLD FILE (DELETED) ---")
        print(df_old.loc[dropped_idx])

    if not added_idx.empty:
        print(f"\n--- ROWS UNIQUE TO NEW FILE (ADDED) ---")
        print(df_new.loc[added_idx])
        
    if not modified_idx.empty:
        print(f"\n--- MODIFIED ROWS (DATA CHANGED) ---")
        print(df_new.loc[modified_idx])

if __name__ == "__main__":
    # Ensure these paths are correct for your Mac environment
    compare_csvs(
        'output/nyt_release_report_2026-03-07.csv', 
        'output/NYT_weekly_sales_report_2026-03-09.csv', 
        'ISBN'
    )