import { useCallback, useEffect, useRef, useState } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useTheme } from '../hooks/useTheme';

interface NavGroup {
  label: string;
  items: NavItem[];
}

interface NavItem {
  to: string;
  label: string;
}

type NavEntry = NavItem | NavGroup;

const NAV_ENTRIES: NavEntry[] = [
  { to: '/', label: 'Overview' },
  {
    label: 'Documents',
    items: [
      { to: '/statements', label: 'Statements' },
      { to: '/eob', label: 'EOB Matching' },
      { to: '/eob/benchmarks', label: 'Benchmarks' },
      { to: '/orphans', label: 'Orphans & Dupes' },
    ],
  },
  {
    label: 'Workflow',
    items: [
      { to: '/action-queue', label: 'Action Queue' },
      { to: '/triage', label: 'Triage' },
      { to: '/corrections', label: 'Corrections' },
    ],
  },
  {
    label: 'Analytics',
    items: [
      { to: '/dashboard-view', label: 'Dashboard' },
      { to: '/insights', label: 'Insights' },
      { to: '/history', label: 'History' },
    ],
  },
  {
    label: 'Admin',
    items: [
      { to: '/rules', label: 'Rules Config' },
      { to: '/settings', label: 'Settings' },
    ],
  },
];

function isNavGroup(entry: NavEntry): entry is NavGroup {
  return 'items' in entry;
}

export function TopNav() {
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const navRef = useRef<HTMLElement>(null);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [expandedMobileGroups, setExpandedMobileGroups] = useState<string[]>([]);

  const isItemActive = useCallback((item: NavItem) => {
    if (item.to === '/') {
      return location.pathname === '/';
    }

    return location.pathname === item.to || location.pathname.startsWith(`${item.to}/`);
  }, [location.pathname]);

  const isGroupActive = useCallback((group: NavGroup) => group.items.some(isItemActive), [isItemActive]);

  const closeMenus = useCallback(() => {
    setOpenGroup(null);
    setIsMobileMenuOpen(false);
  }, []);

  useEffect(() => {
    closeMenus();
  }, [closeMenus, location.pathname]);

  useEffect(() => {
    if (!isMobileMenuOpen) {
      return;
    }

    const activeGroups = NAV_ENTRIES.filter(isNavGroup)
      .filter(isGroupActive)
      .map((group) => group.label);

    if (activeGroups.length === 0) {
      return;
    }

    setExpandedMobileGroups((current) => Array.from(new Set([...current, ...activeGroups])));
  }, [isGroupActive, isMobileMenuOpen]);

  useEffect(() => {
    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      if (!navRef.current?.contains(event.target as Node)) {
        closeMenus();
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('touchstart', handlePointerDown);

    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('touchstart', handlePointerDown);
    };
  }, [closeMenus]);

  const handleGroupToggle = (label: string) => {
    setOpenGroup((current) => (current === label ? null : label));
  };

  const handleMobileGroupToggle = (label: string) => {
    setExpandedMobileGroups((current) =>
      current.includes(label) ? current.filter((groupLabel) => groupLabel !== label) : [...current, label],
    );
  };

  return (
    <nav className="topnav" ref={navRef}>
      <span className="topnav-brand">📄 DI Hub</span>
      <div className="topnav-links">
        {NAV_ENTRIES.map((entry) => {
          if (!isNavGroup(entry)) {
            return (
              <NavLink
                key={entry.to}
                to={entry.to}
                end={entry.to === '/'}
                className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
                onClick={closeMenus}
              >
                {entry.label}
              </NavLink>
            );
          }

          const isOpen = openGroup === entry.label;
          const isActive = isGroupActive(entry);

          return (
            <div key={entry.label} className="topnav-group">
              <button
                type="button"
                className={`topnav-group-toggle topnav-link ${isActive ? 'active' : ''} ${isOpen ? 'open' : ''}`}
                onClick={() => handleGroupToggle(entry.label)}
                aria-expanded={isOpen}
                aria-haspopup="menu"
              >
                {entry.label}
              </button>
              {isOpen ? (
                <div className="topnav-dropdown" role="menu">
                  {entry.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      end
                      role="menuitem"
                      className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
                      onClick={closeMenus}
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      <div className="topnav-actions">
        <button
          type="button"
          className="topnav-hamburger"
          onClick={() => {
            setIsMobileMenuOpen((current) => !current);
            setOpenGroup(null);
          }}
          aria-expanded={isMobileMenuOpen}
          aria-controls="topnav-mobile-menu"
          title="Open navigation menu"
        >
          ☰
        </button>
        <button className="theme-toggle" onClick={toggle} title="Toggle theme">
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>
      {isMobileMenuOpen ? (
        <div className="topnav-mobile-menu" id="topnav-mobile-menu">
          <div className="topnav-mobile-menu-header">
            <span>Navigation</span>
            <button
              type="button"
              className="topnav-mobile-close"
              onClick={() => setIsMobileMenuOpen(false)}
              title="Close navigation menu"
            >
              ✕
            </button>
          </div>
          <div className="topnav-mobile-links">
            {NAV_ENTRIES.map((entry) => {
              if (!isNavGroup(entry)) {
                return (
                  <NavLink
                    key={entry.to}
                    to={entry.to}
                    end={entry.to === '/'}
                    className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
                    onClick={closeMenus}
                  >
                    {entry.label}
                  </NavLink>
                );
              }

              const isExpanded = expandedMobileGroups.includes(entry.label) || isGroupActive(entry);

              return (
                <div key={entry.label} className="topnav-mobile-group">
                  <button
                    type="button"
                    className={`topnav-group-toggle topnav-link ${isGroupActive(entry) ? 'active' : ''} ${isExpanded ? 'open' : ''}`}
                    onClick={() => handleMobileGroupToggle(entry.label)}
                    aria-expanded={isExpanded}
                  >
                    {entry.label}
                  </button>
                  {isExpanded ? (
                    <div className="topnav-mobile-submenu">
                      {entry.items.map((item) => (
                        <NavLink
                          key={item.to}
                          to={item.to}
                          end
                          className={({ isActive }) => `topnav-link ${isActive ? 'active' : ''}`}
                          onClick={closeMenus}
                        >
                          {item.label}
                        </NavLink>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </nav>
  );
}
