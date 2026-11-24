import styles from "./Logo.module.css";

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg
      className={styles.logo}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label="Waypoint"
    >
      <path d="M12 21.5s-7-6.1-7-11.6a7 7 0 0 1 14 0c0 5.5-7 11.6-7 11.6z" />
      <path d="M9.3 7.6l2.3 2.2-2.3 2.2M12.9 12.3h2.3" />
    </svg>
  );
}
