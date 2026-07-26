import { NavLink } from 'react-router-dom';
import { useTheme } from '../hooks/useTheme';

const NAV_LINKS: { to: string; label: string }[] = [
  { to: '/', label: 'Overview' },
  { to: '/dashboard-view', label: 'Dashboard' },
  { to: '/statements', label: 'Statements' },
  { to: '/eob', label: 'EOB Matching' },
  { to: '/action-queue', label: 'Action Queue' },
  { to: '/triage', label: 'Triage' },
  { to: '/corrections', label: 'Corrections' },
  { to: '/insights', label: 'Insights' },
  { to: '/history', label: 'History' },
  { to: '/orphans', label: 'Orphans & Dupes' },
  { to: '/rules', label: 'Rules Config' },
  { to: '/settings', label: 'Settings' },
];

export function TopNav() {
  const { theme, toggle } = useTheme();
  return (
    <nav className="topnav">
      <span className="topnav-brand">📄 DI Hub</span>
      <div className="topnav-links">
        {NAV_LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.to === '/'}
            className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
          >
            {link.label}
          </NavLink>
        ))}
      </div>
      <div className="topnav-actions">
        <button className="theme-toggle" onClick={toggle} title="Toggle theme">
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>
    </nav>
  );
}
