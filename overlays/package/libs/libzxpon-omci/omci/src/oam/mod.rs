// SPDX-License-Identifier: GPL-2.0-only

//! The OAM sessions: IEEE 802.3ah, and the CTC private extension.
//!
//! Vendored from pbs05/openwrt-pon-userspace, from
//! airoha-pon-daemons/src/src/oam/.  The upstream module also carries the
//! daemon that runs the session - a raw socket on the PON control
//! interface, a status file, a control socket - and that daemon is not
//! taken: it belongs to a daemon this board does not run.  What is left
//! is the session itself.
//!
//! The session rides an IEEE 802.3ah Ethernet OAMPDU.  This board is
//! XG-PON: it carries PLOAM and OMCI, and it has no way to send an
//! Ethernet frame to the OLT.  So nothing here turns the session on, and
//! the code is here so that the day such a channel exists, the session
//! comes with it rather than being written again.

mod config;
mod ctc;
mod frame;
mod ieee;

pub use config::{fixed_width, fixed_width_right, OamConfig};
pub use ctc::CtcSession;
pub use frame::{
    build, OamPdu, CODE_EVENT_NOTIFICATION, CODE_INFORMATION, CODE_LOOPBACK_CONTROL,
    CODE_ORGANIZATION_SPECIFIC, FLAGS_STABLE, SLOW_PROTOCOLS_MULTICAST,
};
pub use ieee::IeeeSession;
