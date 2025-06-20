"""Core IT8951 e-paper controller driver."""

import time

from IT8951_ePaper_Py.constants import (
    DisplayConstants,
    PixelFormat,
    PowerState,
    ProtocolConstants,
    Register,
    SystemCommand,
    TimingConstants,
    UserCommand,
)
from IT8951_ePaper_Py.exceptions import (
    DeviceError,
    InitializationError,
    InvalidParameterError,
    IT8951MemoryError,
    IT8951TimeoutError,
    VCOMError,
)
from IT8951_ePaper_Py.models import (
    AreaImageInfo,
    DeviceInfo,
    DisplayArea,
    LoadImageInfo,
    VCOMConfig,
)
from IT8951_ePaper_Py.spi_interface import SPIInterface, create_spi_interface


class IT8951:
    """IT8951 e-paper controller driver."""

    def __init__(self, spi_interface: SPIInterface | None = None) -> None:
        """Initialize IT8951 driver.

        Args:
            spi_interface: Optional SPI interface. If not provided, will create
                          appropriate interface based on platform.
        """
        self._spi = spi_interface or create_spi_interface()
        self._device_info: DeviceInfo | None = None
        self._initialized = False
        self._power_state = PowerState.ACTIVE

    @property
    def power_state(self) -> PowerState:
        """Get the current power state of the device.

        Returns:
            PowerState: The current power state (ACTIVE, STANDBY, or SLEEP).
        """
        return self._power_state

    def init(self) -> DeviceInfo:
        """Initialize the IT8951 controller.

        Returns:
            Device information.

        Raises:
            InitializationError: If initialization fails.
        """
        if self._initialized:
            return self._device_info or self._get_device_info()

        try:
            self._spi.init()
            self._system_run()
            self._device_info = self._get_device_info()
            self._enable_packed_write()
            self._initialized = True
            return self._device_info
        except Exception as e:
            self.close()
            raise InitializationError(f"Failed to initialize IT8951: {e}") from e

    def close(self) -> None:
        """Close the driver and cleanup resources."""
        if self._spi:
            self._spi.close()
        self._initialized = False
        self._device_info = None
        self._power_state = PowerState.ACTIVE

    def _system_run(self) -> None:
        """Send system run command."""
        self._spi.write_command(SystemCommand.SYS_RUN)
        self._power_state = PowerState.ACTIVE

    def _get_device_info(self) -> DeviceInfo:
        """Get device information from controller.

        Returns:
            Device information.
        """
        self._spi.write_command(UserCommand.GET_DEV_INFO)
        data = self._spi.read_data_bulk(ProtocolConstants.DEVICE_INFO_SIZE)

        fw_version: list[int] = []
        lut_version: list[int] = []

        for i in range(8):
            fw_version.append(data[i + 4])
            lut_version.append(data[i + 12])

        return DeviceInfo(
            panel_width=data[0],
            panel_height=data[1],
            memory_addr_l=data[2],
            memory_addr_h=data[3],
            fw_version=fw_version,
            lut_version=lut_version,
        )

    def _enable_packed_write(self) -> None:
        """Enable packed write mode for better performance."""
        reg_value = self._read_register(Register.REG_0204)
        reg_value |= ProtocolConstants.PACKED_WRITE_BIT
        self._write_register(Register.REG_0204, reg_value)

    def _read_register(self, register: int) -> int:
        """Read a register value.

        Args:
            register: Register address.

        Returns:
            int: Register value (16-bit).
        """
        self._spi.write_command(SystemCommand.REG_RD)
        self._spi.write_data(register)
        return self._spi.read_data()

    def _write_register(self, register: int, value: int) -> None:
        """Write a register value.

        Args:
            register: Register address.
            value: Value to write.
        """
        self._spi.write_command(SystemCommand.REG_WR)
        self._spi.write_data(register)
        self._spi.write_data(value)

    def _write_command_with_args(self, command: int, args: list[int]) -> None:
        """Write a command followed by multiple data arguments.

        Args:
            command: Command to send.
            args: List of data arguments to send after the command.
        """
        self._spi.write_command(command)
        for arg in args:
            self._spi.write_data(arg)

    def dump_registers(self) -> dict[str, int]:
        """Dump common register values for debugging.

        Returns:
            dict[str, int]: Dictionary of register names to values.
        """
        self._ensure_initialized()

        registers = {
            "LISAR": Register.LISAR,
            "REG_0204": Register.REG_0204,
            "MISC": Register.MISC,
            "PWR": Register.PWR,
            "MCSR": Register.MCSR,
            "ENHANCE_DRIVING": Register.ENHANCE_DRIVING,
        }

        dump: dict[str, int] = {}
        for name, address in registers.items():
            try:
                value = self._read_register(address)
                dump[name] = value
            except Exception:
                # If register read fails, mark as unreadable
                dump[name] = -1

        return dump

    def check_lut_busy(self) -> bool:
        """Check if LUT engine is busy.

        Returns:
            bool: True if LUT is busy, False otherwise.
        """
        self._ensure_initialized()
        misc_value = self._read_register(Register.MISC)
        # Bit 7 indicates LUT busy state
        return (misc_value & ProtocolConstants.LUT_BUSY_BIT) != 0

    def verify_packed_write_enabled(self) -> bool:
        """Verify that packed write mode is enabled.

        Returns:
            bool: True if packed write is enabled, False otherwise.
        """
        self._ensure_initialized()
        reg_value = self._read_register(Register.REG_0204)
        return (reg_value & ProtocolConstants.PACKED_WRITE_BIT) != 0

    def get_memory_address(self) -> int:
        """Get current target memory address from LISAR registers.

        Returns:
            int: Current memory address (32-bit).
        """
        self._ensure_initialized()
        # Read low 16 bits
        low = self._read_register(Register.LISAR)
        # Read high 16 bits
        high = self._read_register(Register.LISAR + 2)
        return (high << 16) | low

    def standby(self) -> None:
        """Put device into standby mode."""
        self._ensure_initialized()
        self._spi.write_command(SystemCommand.STANDBY)
        self._power_state = PowerState.STANDBY

    def sleep(self) -> None:
        """Put device into sleep mode."""
        self._ensure_initialized()
        self._spi.write_command(SystemCommand.SLEEP)
        self._power_state = PowerState.SLEEP

    def wake(self) -> None:
        """Wake device from sleep or standby mode.

        After waking, the device returns to normal operation mode.
        This is typically called after sleep() or standby() to resume display operations.
        """
        self._ensure_initialized()
        # Wake is typically done by sending any command, so we use system run
        self._system_run()
        self._power_state = PowerState.ACTIVE

    def enhance_driving_capability(self) -> None:
        """Enhance driving capability for long cables or blurry displays.

        This sets the enhanced driving register to improve signal quality,
        which can help with:
        - Blurry or unclear display output
        - Long FPC cable connections
        - Display instability issues

        Note: This should be called after initialization but before
        displaying images.
        """
        self._ensure_initialized()
        self._write_register(Register.ENHANCE_DRIVING, ProtocolConstants.ENHANCED_DRIVING_VALUE)

    def is_enhanced_driving_enabled(self) -> bool:
        """Check if enhanced driving capability is enabled.

        Returns:
            bool: True if enhanced driving is enabled (0x0602), False otherwise.
        """
        self._ensure_initialized()
        value = self._read_register(Register.ENHANCE_DRIVING)
        return value == ProtocolConstants.ENHANCED_DRIVING_VALUE

    def get_vcom(self) -> float:
        """Get current VCOM voltage.

        Returns:
            VCOM voltage in volts.
        """
        self._ensure_initialized()
        self._spi.write_command(UserCommand.VCOM)
        self._spi.write_data(0)
        vcom_raw = self._spi.read_data()
        return -vcom_raw / ProtocolConstants.VCOM_FACTOR

    def set_vcom(self, voltage: float) -> None:
        """Set VCOM voltage.

        Args:
            voltage: VCOM voltage in volts (must be negative).

        Raises:
            VCOMError: If voltage is out of range or configuration fails.
        """
        self._ensure_initialized()
        try:
            config = VCOMConfig(voltage=voltage)
        except Exception as e:
            raise VCOMError(
                f"Invalid VCOM voltage: {voltage}V. "
                f"VCOM must be between {DisplayConstants.MIN_VCOM}V and "
                f"{DisplayConstants.MAX_VCOM}V. "
                f"Check the VCOM value on your display's FPC cable. Error: {e}"
            ) from e
        vcom_raw = int(-config.voltage * ProtocolConstants.VCOM_FACTOR)
        self._spi.write_command(UserCommand.VCOM)
        self._spi.write_data(1)
        self._spi.write_data(vcom_raw)

    def set_target_memory_addr(self, address: int) -> None:
        """Set target memory address for image loading.

        Args:
            address: Target memory address.

        Raises:
            IT8951MemoryError: If address is invalid.
        """
        self._ensure_initialized()

        # Validate memory address
        if address < 0 or address > ProtocolConstants.MAX_ADDRESS:
            raise IT8951MemoryError(f"Invalid memory address: 0x{address:08X}")

        self._write_register(Register.LISAR, address & ProtocolConstants.ADDRESS_MASK)
        self._write_register(
            Register.LISAR + ProtocolConstants.LISAR_HIGH_OFFSET,
            (address >> (ProtocolConstants.BYTE_SHIFT * 2)) & ProtocolConstants.ADDRESS_MASK,
        )

    def load_image_start(self, info: LoadImageInfo) -> None:
        """Start loading an image to controller memory.

        Args:
            info: Image loading information.
        """
        self._ensure_initialized()
        self.set_target_memory_addr(info.target_memory_addr)

        args = [
            info.endian_type,
            info.pixel_format,
            info.rotate,
            0,
            0,
        ]

        self._write_command_with_args(SystemCommand.LD_IMG, args)

    def load_image_area_start(self, info: LoadImageInfo, area: AreaImageInfo) -> None:
        """Start loading an image area to controller memory.

        Args:
            info: Image loading information.
            area: Area information.
        """
        self._ensure_initialized()
        self.set_target_memory_addr(info.target_memory_addr)

        args = [
            info.endian_type,
            info.pixel_format,
            info.rotate,
            area.x,
            area.y,
            area.width,
            area.height,
        ]

        self._write_command_with_args(SystemCommand.LD_IMG_AREA, args)

    def load_image_write(self, data: bytes) -> None:
        """Write image data to controller.

        Args:
            data: Image data bytes.
        """
        self._ensure_initialized()

        words: list[int] = []
        for i in range(0, len(data), 2):
            word = (data[i] << 8) | data[i + 1] if i + 1 < len(data) else data[i] << 8
            words.append(word)

        self._spi.write_command(SystemCommand.MEM_BST_WR)
        self._spi.write_data_bulk(words)

    @staticmethod
    def convert_endian_1bpp(data: bytes, reverse_bits: bool = False) -> bytes:
        """Convert endianness for 1bpp data.

        Args:
            data: 1bpp packed data.
            reverse_bits: If True, reverse bit order within each byte.
                         This swaps between MSB-first and LSB-first bit ordering.

        Returns:
            Converted data.
        """
        if not reverse_bits:
            return data

        # Reverse bits in each byte
        result: list[int] = []
        for byte in data:
            # Reverse bits: 0b10110010 -> 0b01001101
            reversed_byte = 0
            for i in range(8):
                if byte & (1 << i):
                    reversed_byte |= 1 << (7 - i)
            result.append(reversed_byte)
        return bytes(result)

    @staticmethod
    def pack_pixels(pixels: bytes, pixel_format: PixelFormat) -> bytes:
        """Pack pixel data according to the specified format.

        Args:
            pixels: 8-bit pixel data (each byte is one pixel).
            pixel_format: Target pixel format.

        Returns:
            Packed pixel data according to format.

        Raises:
            InvalidParameterError: If pixel format is not supported.
        """
        # Use dictionary dispatch for cleaner code and lower complexity
        packers = {
            PixelFormat.BPP_8: IT8951._pack_8bpp,
            PixelFormat.BPP_4: IT8951._pack_4bpp,
            PixelFormat.BPP_2: IT8951._pack_2bpp,
            PixelFormat.BPP_1: IT8951._pack_1bpp,
        }

        packer = packers.get(pixel_format)
        if not packer:
            raise InvalidParameterError(f"Pixel format {pixel_format} not yet implemented")

        return packer(pixels)

    @staticmethod
    def _pack_8bpp(pixels: bytes) -> bytes:
        """No packing needed for 8bpp."""
        return pixels

    @staticmethod
    def _pack_4bpp(pixels: bytes) -> bytes:
        """Pack 2 pixels per byte (4 bits each)."""
        packed: list[int] = []
        for i in range(0, len(pixels), 2):
            # Each pixel is reduced to 4 bits (0-15 range)
            pixel1 = (pixels[i] >> 4) if i < len(pixels) else 0
            pixel2 = (pixels[i + 1] >> 4) if i + 1 < len(pixels) else 0
            # Pack two pixels into one byte (pixel1 in high nibble, pixel2 in low nibble)
            packed_byte = (pixel1 << 4) | pixel2
            packed.append(packed_byte)
        return bytes(packed)

    @staticmethod
    def _pack_2bpp(pixels: bytes) -> bytes:
        """Pack 4 pixels per byte (2 bits each)."""
        packed: list[int] = []
        for i in range(0, len(pixels), 4):
            # Each pixel is reduced to 2 bits (0-3 range)
            pixel1 = (pixels[i] >> 6) if i < len(pixels) else 0
            pixel2 = (pixels[i + 1] >> 6) if i + 1 < len(pixels) else 0
            pixel3 = (pixels[i + 2] >> 6) if i + 2 < len(pixels) else 0
            pixel4 = (pixels[i + 3] >> 6) if i + 3 < len(pixels) else 0
            # Pack four pixels into one byte
            packed_byte = (pixel1 << 6) | (pixel2 << 4) | (pixel3 << 2) | pixel4
            packed.append(packed_byte)
        return bytes(packed)

    @staticmethod
    def _pack_1bpp(pixels: bytes) -> bytes:
        """Pack 8 pixels per byte (1 bit each)."""
        packed: list[int] = []
        for i in range(0, len(pixels), 8):
            packed_byte = 0
            for j in range(8):
                if i + j < len(pixels):
                    # Convert to 1-bit (0 or 1) - threshold at 128
                    bit = 1 if pixels[i + j] >= 128 else 0
                    # Pack bit into byte (MSB first)
                    packed_byte |= bit << (7 - j)
            packed.append(packed_byte)
        return bytes(packed)

    def load_image_end(self) -> None:
        """End image loading operation."""
        self._ensure_initialized()
        self._spi.write_command(SystemCommand.LD_IMG_END)

    def _validate_display_area(self, area: DisplayArea) -> None:
        """Validate display area bounds.

        Args:
            area: Display area to validate.

        Raises:
            DeviceError: If device info not available.
            InvalidParameterError: If area exceeds panel bounds.
        """
        if not self._device_info:
            raise DeviceError("Device info not available")

        if area.x + area.width > self._device_info.panel_width:
            raise InvalidParameterError("Display area exceeds panel width")
        if area.y + area.height > self._device_info.panel_height:
            raise InvalidParameterError("Display area exceeds panel height")

    def display_area(self, area: DisplayArea, wait: bool = True) -> None:
        """Display an area with specified mode.

        Args:
            area: Display area configuration.
            wait: Whether to wait for display to complete.
        """
        self._ensure_initialized()
        self._validate_display_area(area)

        args = [
            area.x,
            area.y,
            area.width,
            area.height,
            area.mode,
        ]

        self._write_command_with_args(UserCommand.DPY_AREA, args)

        if wait:
            self._wait_display_ready()

    def display_buffer_area(self, area: DisplayArea, address: int, wait: bool = True) -> None:
        """Display an area from a specific buffer address.

        Args:
            area: Display area configuration.
            address: Buffer memory address.
            wait: Whether to wait for display to complete.
        """
        self._ensure_initialized()
        self._validate_display_area(area)

        args = [
            area.x,
            area.y,
            area.width,
            area.height,
            area.mode,
            address & ProtocolConstants.ADDRESS_MASK,
            (address >> (ProtocolConstants.BYTE_SHIFT * 2)) & ProtocolConstants.ADDRESS_MASK,
        ]

        self._write_command_with_args(UserCommand.DPY_BUF_AREA, args)

        if wait:
            self._wait_display_ready()

    def _wait_display_ready(self, timeout_ms: int = TimingConstants.DISPLAY_TIMEOUT_MS) -> None:
        """Wait for display operation to complete.

        Args:
            timeout_ms: Timeout in milliseconds.

        Raises:
            IT8951TimeoutError: If timeout occurs.
        """
        start_time = time.time()
        while time.time() - start_time < timeout_ms / 1000:
            lut_state = (
                self._read_register(Register.MISC) >> ProtocolConstants.LUT_STATE_BIT_POSITION
            )
            if lut_state == 0:
                return
            time.sleep(TimingConstants.DISPLAY_POLL_S)

        raise IT8951TimeoutError(f"Display operation timed out after {timeout_ms}ms")

    def _ensure_initialized(self) -> None:
        """Ensure the driver is initialized.

        Raises:
            InitializationError: If not initialized.
        """
        if not self._initialized:
            raise InitializationError("IT8951 not initialized. Call init() first.")

    @property
    def device_info(self) -> DeviceInfo:
        """Get device information.

        Returns:
            Device information.

        Raises:
            InitializationError: If not initialized.
        """
        self._ensure_initialized()
        if not self._device_info:
            raise InitializationError("Device info not available")
        return self._device_info
