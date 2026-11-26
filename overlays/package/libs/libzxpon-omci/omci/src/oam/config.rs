// SPDX-License-Identifier: GPL-2.0-only

//! The configuration of the OAM sessions.
//!
//! Vendored from pbs05/openwrt-pon-userspace, from
//! airoha-pon-daemons/src/src/oam/config.rs, and adapted: the upstream
//! file reads its values out of that daemon's own configuration parser,
//! and this crate has no such parser.  What is left is the configuration
//! itself and the rule that derives the ONU serial number out of it.

use crate::config::IdentityConfig;

#[derive(Clone, Debug)]
pub struct OamConfig {
    pub operator: String,
    pub ctc_oui: [u8; 3],
    pub ctc_versions: Vec<u8>,
    pub vendor_id: Vec<u8>,
    pub model: Vec<u8>,
    pub equipment_id: Vec<u8>,
    pub hardware_version: Vec<u8>,
    pub software_version: Vec<u8>,
    pub firmware_version: Vec<u8>,
    pub chipset_id: [u8; 8],
    pub loid: Vec<u8>,
    pub loid_password: Vec<u8>,
    pub ge_ports: u8,
    pub active_serial_number: Option<[u8; 8]>,
}

impl Default for OamConfig {
    fn default() -> Self {
        Self {
            // The CTC session is off unless something turns it on.  Upstream
            // defaults the operator to "ctc", because upstream runs the
            // session on an Ethernet OAMPDU channel in EPON mode; this board
            // is XG-PON and has no way to carry such a frame, so a session
            // that was on by default would only ever wait for frames that
            // cannot arrive.
            operator: "ieee".to_owned(),
            ctc_oui: [0x11, 0x11, 0x11],
            ctc_versions: vec![0x21, 0x30],
            vendor_id: b"OWRT".to_vec(),
            model: b"ZX279133".to_vec(),
            equipment_id: b"ZX279133".to_vec(),
            hardware_version: b"ZX279133".to_vec(),
            software_version: b"zxpon-1.0".to_vec(),
            firmware_version: b"zxpon-1.0".to_vec(),
            chipset_id: [0x00; 8],
            loid: Vec::new(),
            loid_password: Vec::new(),
            ge_ports: 1,
            active_serial_number: None,
        }
    }
}

impl OamConfig {
    /// The configuration of the OAM session, out of the identity the board
    /// was given.
    ///
    /// The serial number is the one the board carries: the configuration
    /// either names it or derives it, and this crate is handed the result
    /// either way.  It is the active serial number, so the CTC leaf that
    /// asks for it gets the very bytes the PLOAM SERIAL_NUMBER carries.
    pub fn from_identity(identity: &IdentityConfig) -> Self {
        let mut config = Self::default();
        config.vendor_id = identity.vendor_id.clone();
        config.equipment_id = identity.equipment_id.clone();
        config.hardware_version = identity.hardware_version.clone();
        config.software_version = identity.software_version.clone();
        config.firmware_version = identity.software_version.clone();
        config.loid = identity.loid.clone();
        config.loid_password = identity.loid_password.clone();
        config.active_serial_number = match identity.serial_number.as_slice() {
            [a, b, c, d, e, f, g, h] => Some([*a, *b, *c, *d, *e, *f, *g, *h]),
            _ => None,
        };
        config
    }

    pub fn ctc_enabled(&self) -> bool {
        self.operator == "ctc"
    }

    /// The rule the upstream engine uses for the ONU serial number: the
    /// serial number the station was given, and otherwise the vendor id
    /// followed by the last four bytes of the station's own MAC address.
    pub fn onu_serial(&self, source_mac: [u8; 6]) -> [u8; 8] {
        if let Some(serial) = self.active_serial_number {
            return serial;
        }

        let mut serial = [0u8; 8];
        let vendor = fixed_width(&self.vendor_id, 4);
        serial[..4].copy_from_slice(&vendor);
        serial[4..].copy_from_slice(&source_mac[2..]);
        serial
    }
}

pub fn fixed_width(value: &[u8], width: usize) -> Vec<u8> {
    let mut output = vec![0; width];
    let copied = value.len().min(width);
    output[..copied].copy_from_slice(&value[..copied]);
    output
}

pub fn fixed_width_right(value: &[u8], width: usize) -> Vec<u8> {
    let mut output = vec![0; width];
    let copied = value.len().min(width);
    output[width - copied..].copy_from_slice(&value[..copied]);
    output
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    /// The vectors the C side checks as well: one file, one rule, two
    /// implementations.  A rule that drifted on one side would show up
    /// here as a different answer for the same bytes.
    fn vectors() -> String {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../../../tests/fixtures/onu-serial-vectors.txt");
        std::fs::read_to_string(&path).expect("the serial vectors are in the tree")
    }

    fn hex(text: &str) -> Vec<u8> {
        (0..text.len() / 2)
            .map(|index| u8::from_str_radix(&text[index * 2..index * 2 + 2], 16).expect("hex"))
            .collect()
    }

    #[test]
    fn the_serial_rule_is_the_one_the_c_side_implements() {
        let mut checked = 0;
        for line in vectors().lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let fields: Vec<&str> = line.split_whitespace().collect();
            assert_eq!(fields.len(), 3, "a vector has a vendor, a MAC and a serial");
            let vendor = hex(fields[0]);
            let mac = hex(fields[1]);
            let expected = hex(fields[2]);
            let mut config = OamConfig::default();
            config.vendor_id = vendor.clone();
            config.active_serial_number = None;
            let mut address = [0u8; 6];
            address.copy_from_slice(&mac);
            assert_eq!(config.onu_serial(address).to_vec(), expected);
            checked += 1;
        }
        assert!(checked > 0, "there are vectors to check");
    }

    #[test]
    fn the_active_serial_number_wins_over_the_derived_one() {
        let mut config = OamConfig::default();
        config.active_serial_number = Some(*b"HWTC1234");

        assert_eq!(config.onu_serial([0; 6]), *b"HWTC1234");
    }

    #[test]
    fn the_ctc_session_is_off_unless_something_turns_it_on() {
        assert!(!OamConfig::default().ctc_enabled());

        let mut config = OamConfig::default();
        config.operator = "ctc".to_owned();
        assert!(config.ctc_enabled());
    }

    #[test]
    fn the_identity_the_board_carries_is_the_active_serial_number() {
        let identity = IdentityConfig {
            omcc_version: 0xb0,
            disable_enhanced_security: false,
            vendor_id: b"HWTC".to_vec(),
            equipment_id: b"SK-D840N".to_vec(),
            hardware_version: b"ZX279133".to_vec(),
            software_version: b"zxpon-1.0".to_vec(),
            loid: Vec::new(),
            loid_password: Vec::new(),
            operator_id: b"CTC".to_vec(),
            serial_number: b"HWTC1234".to_vec(),
            psk: None,
        };
        let config = OamConfig::from_identity(&identity);

        assert_eq!(config.active_serial_number, Some(*b"HWTC1234"));
        assert_eq!(config.onu_serial([0x02, 0x11, 0x22, 0x33, 0x44, 0x55]), *b"HWTC1234");
    }
}
