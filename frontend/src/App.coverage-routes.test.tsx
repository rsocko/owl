import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { LegacyStatementSeriesRedirect, LegacyStatementsRedirect } from './App';

function Location() {
  const location = useLocation();
  return <div>{location.pathname}{location.search}</div>;
}

describe('legacy statement routes', () => {
  it('redirects the overview to statement-filtered Coverage', async () => {
    render(<MemoryRouter initialEntries={['/statements']}><Routes><Route path="/statements" element={<LegacyStatementsRedirect />} /><Route path="/coverage" element={<Location />} /></Routes></MemoryRouter>);
    expect(await screen.findByText('/coverage?kind=statement')).toBeInTheDocument();
  });

  it('redirects series detail to the canonical Coverage editor', async () => {
    render(<MemoryRouter initialEntries={['/statements/checking']}><Routes><Route path="/statements/:seriesId" element={<LegacyStatementSeriesRedirect />} /><Route path="/coverage/series/:seriesId" element={<Location />} /></Routes></MemoryRouter>);
    expect(await screen.findByText('/coverage/series/checking')).toBeInTheDocument();
  });
});
