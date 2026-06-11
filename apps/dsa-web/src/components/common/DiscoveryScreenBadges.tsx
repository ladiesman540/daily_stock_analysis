import type React from 'react';
import type { DiscoveryScreenBadge } from '../../utils/discoveryScreens';
import { Badge } from './Badge';
import { InfoTip } from './InfoTip';

/** Renders the screens-passed list as small InfoTip'd badges. */
export const DiscoveryScreenBadges: React.FC<{ screens: DiscoveryScreenBadge[] }> = ({ screens }) => (
  <>
    {screens.map((screen) => (
      <InfoTip key={screen.key} term={screen.term} title={screen.title} body={screen.body}>
        <Badge variant="default" className="text-[11px]">{screen.label}</Badge>
      </InfoTip>
    ))}
  </>
);
