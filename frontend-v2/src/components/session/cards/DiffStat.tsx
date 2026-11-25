import styles from "./DiffStat.module.css";

export function DiffStat({ additions, deletions }: { additions: number; deletions: number }) {
  return (
    <>
      <span className={styles.add}>+{additions}</span> <span className={styles.del}>−{deletions}</span>
    </>
  );
}
