# Hwam_stove component for Home Assistant

This checkout prepares **Saynwerk HWAM Smart Stove 1.0.0rc1** for private local
testing, based on the original [mvn23/hwam_stove](https://github.com/mvn23/hwam_stove).
No license is added or implied. Public redistribution/publication remains blocked
until licensing of the inherited code is clarified. A prerelease on a public GitHub
repository is public too; HACS does not support private GitHub repositories.
See the [RC preparation and existing-entry migration plan](docs/SAYNWERK_PRIVATE_RC1.md).

The `hwam_stove` component is used to control a [Hwam Stove with Smartcontrol](http://www.hwam.com/) from Home Assistant.

## Configuration

For an existing HWAM installation, preserve its config entry: do **not** use the
new-integration button below. Follow the migration plan linked above. The following
upstream setup instructions apply only to a new installation, after distribution
and installation have been separately approved.

First, add this github repository as a custom HACS repository as per [the HACS documentation](https://hacs.xyz/docs/faq/custom_repositories/).
After that, you can use the button below to configure the integration.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hwam_stove)
