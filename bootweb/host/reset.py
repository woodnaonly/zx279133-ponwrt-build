"""Strict read-only control-DTB Reset mapping, not Linux GPIO numbering."""
import struct
import math

CONTROL_DTB_SHA256="330630bb6cfe276d6cc66784a6ff970183ec8dca9af90b0b9ba1bccda968d007"
PINCTRL_CODE_PINS=((0x522e4,0x68),(0x523ac,0xb8),(0x525e8,0x268))

def unverified_reset_mapping(report):
    """Retain measured mux/direction facts WITHOUT claiming a button test.

    Only used for an explicitly user-directed unaccepted installation. It is
    not a substitute input to physical_reset_evidence or any acceptance gate.
    """
    if (report.get("schema")!="sk-d840n-reset-physical-pinctrl-v2" or
            report.get("mmio_access_width")!=32 or report.get("gpio_written") is not False or
            report.get("nand_written") is not False or report.get("pinctrl_restored") is not True or
            report.get("pinctrl_runtime_code_verified") is not True or
            report.get("control_dtb_sha256")!=CONTROL_DTB_SHA256 or
            report.get("gpio_registers",{}).get("0x0")!="0x2600"):
        raise ValueError("unverified Reset build still requires exact live 32-bit mapping/restoration")
    expected={"register":"0x10e20000","global_pin":14,"shift":29,"width":2,
              "mask":"0x60000000","function":1,"value":"0x20000000"}
    mapping=report.get("pinctrl_configuration",{})
    before,configured,after=(report.get(k) for k in ("pinctrl_before","pinctrl_configured","pinctrl_after"))
    if (any(mapping.get(k)!=v for k,v in expected.items()) or
            any(type(v) is not int or not 0<=v<=0xffffffff for v in (before,configured,after)) or
            after!=before or configured!=((before&~0x60000000)|0x20000000)):
        raise ValueError("unverified Reset mux changed unrelated bits or was not restored")
    return {"physical_reset_pass":False,"physical_acceptance_waived":True,
            "pinctrl_configuration":dict(mapping),"control_dtb_sha256":CONTROL_DTB_SHA256}

def physical_reset_evidence(report):
    """Recompute release/continuous press/release; a boolean cannot unlock it."""
    schema=report.get("schema")
    if schema not in ("sk-d840n-reset-physical-readonly-v1","sk-d840n-reset-physical-pinctrl-v2") or report.get("physical_reset_pass") is not True or report.get("gpio_written") is not False or report.get("nand_written") is not False or report.get("mmio_access_width")!=32 or report.get("control_dtb_sha256")!=CONTROL_DTB_SHA256:
        raise ValueError("physical Reset evidence is missing or belongs to a different control DTB")
    pinctrl=None
    if schema=="sk-d840n-reset-physical-pinctrl-v2":
        pinctrl=report.get("pinctrl_configuration",{})
        expected={"register":"0x10e20000","global_pin":14,"shift":29,"width":2,
                  "mask":"0x60000000","function":1,"value":"0x20000000"}
        if (any(pinctrl.get(k)!=v for k,v in expected.items()) or
                report.get("mmio_access_width")!=32 or report.get("pinctrl_restored") is not True or report.get("pinctrl_runtime_code_verified") is not True):
            raise ValueError("volatile pinctrl mapping/code/restoration was not verified")
        before,configured,after=(report.get(k) for k in ("pinctrl_before","pinctrl_configured","pinctrl_after"))
        if any(type(v) is not int or not 0<=v<=0xffffffff for v in (before,configured,after)) or after!=before or configured!=((before&~0x60000000)|0x20000000):
            raise ValueError("pinctrl trace changes unrelated bits or lacks exact restoration")
    mapping=report.get("reset_mapping",{})
    if mapping.get("register")!="0x10d10014" or mapping.get("bit")!=0 or mapping.get("active_low") is not True or report.get("gpio_registers",{}).get("0x0")!="0x2600":
        raise ValueError("Reset polarity/controller/direction was not measured as expected")
    samples=report.get("samples",[])
    last=None;released=False;pressed=None;held=False;complete=False
    if not 3<=len(samples)<=4000:raise ValueError("missing/bounded physical Reset trace")
    for row in samples:
        if not isinstance(row,list) or len(row)!=2:raise ValueError("invalid Reset sample")
        stamp,value=row
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0 or type(value) is not int or value not in (0,1):raise ValueError("invalid Reset timestamp/value")
        if last is not None and (stamp<=last or stamp-last>.25):raise ValueError("gapped/nonmonotonic Reset sampling")
        last=stamp
        if not released:
            if value==1:released=True
            continue
        if value==0:
            if pressed is None:pressed=stamp
            if stamp-pressed>=3:held=True
        else:
            if held:complete=True
            pressed=None
    if not complete:raise ValueError("Reset trace lacks release, continuous 3-second press, then release")
    result={"physical_reset_pass":True,"register":"0x10d10014","bit":0,"active_low":True,
            "gpio_written":False,"samples":len(samples),"control_dtb_sha256":CONTROL_DTB_SHA256}
    if pinctrl is not None:result["pinctrl_configuration"]=dict(pinctrl)
    return result

def properties(blob):
    if len(blob)<40:raise ValueError("short control DTB")
    magic,total,off_struct,off_strings,off_reserved,version,last,boot,strings_n,struct_n=struct.unpack_from(">10I",blob)
    if magic!=0xd00dfeed or total!=len(blob) or version<17 or last>17 or off_struct%4 or off_strings<40 or off_struct<40 or off_struct+struct_n>total or off_strings+strings_n>total:
        raise ValueError("invalid control DTB extents")
    strings=blob[off_strings:off_strings+strings_n];at=off_struct;end=at+struct_n;stack=[];nodes={}
    def word():
        nonlocal at
        if at+4>end:raise ValueError("truncated DTB token")
        n=struct.unpack_from(">I",blob,at)[0];at+=4;return n
    while at<end:
        token=word()
        if token==1:
            stop=blob.find(b"\0",at,end)
            if stop<0:raise ValueError("unterminated DTB node")
            name=blob[at:stop].decode("ascii");at=(stop+4)&~3
            if "/" in name or (not stack and name):raise ValueError("invalid DTB node name")
            stack.append(name);path="/"+"/".join(stack[1:])
            if path in nodes:raise ValueError("duplicate DTB node")
            nodes[path]={}
        elif token==2:
            if not stack:raise ValueError("unbalanced DTB nodes")
            stack.pop()
        elif token==3:
            n=word();name_at=word()
            if not stack or at+n>end or name_at>=len(strings):raise ValueError("invalid DTB property")
            stop=strings.find(b"\0",name_at)
            if stop<0:raise ValueError("unterminated property name")
            name=strings[name_at:stop].decode("ascii");path="/"+"/".join(stack[1:])
            if name in nodes[path]:raise ValueError("duplicate DTB property")
            nodes[path][name]=blob[at:at+n];at=(at+n+3)&~3
        elif token==4:continue
        elif token==9:
            if stack:raise ValueError("unbalanced end of DTB")
            return nodes
        else:raise ValueError("invalid DTB token")
    raise ValueError("control DTB has no end token")

def reset_mapping(blob):
    nodes=properties(blob);key=nodes["/soc/keys/keys@restore"]
    if len(key["gpios"])!=12:raise ValueError("Reset GPIO is not phandle/line/flags")
    phandle,line,flags=struct.unpack(">III",key["gpios"])
    matches=[(path,p) for path,p in nodes.items() if p.get("phandle")==struct.pack(">I",phandle)]
    if len(matches)!=1:raise ValueError("Reset GPIO controller is ambiguous")
    path,controller=matches[0];parent=nodes[path.rsplit("/",1)[0] or "/"]
    ac=struct.unpack(">I",parent.get("#address-cells",b"\0\0\0\2"))[0]
    sc=struct.unpack(">I",parent.get("#size-cells",b"\0\0\0\1"))[0]
    reg=controller["reg"]
    if ac not in (1,2) or sc not in (1,2) or len(reg)!=(ac+sc)*4:raise ValueError("unverified Reset GPIO register mapping")
    base=int.from_bytes(reg[:ac*4],"big");size=int.from_bytes(reg[ac*4:],"big")
    if base!=0x10d10000 or size<0x18 or line!=0 or flags!=1 or controller.get("#gpio-cells")!=b"\0\0\0\2":
        raise ValueError("Reset input differs from audited GPIO callback/control DTB")
    return {"key_path":"/soc/keys/keys@restore","controller_path":path,"phandle":phandle,
            "base":hex(base),"input_offset":"0x14","register":hex(base+0x14),"bit":line,"active_low":True}


def reset_pinctrl_mapping(blob):
    """Mirror the pinned vendor GPIO request and variable-width bank parser.

    A Linux GPIO offset is not a global mux pin. Only the measured controller,
    pin, bank, field and function are accepted; no caller-supplied MMIO target.
    """
    mapping=reset_mapping(blob);nodes=properties(blob)
    def cells(value):
        if len(value)%4:raise ValueError("unaligned pinctrl cells")
        return struct.unpack(">"+str(len(value)//4)+"I",value)
    def phandle(value):
        found=[(path,p) for path,p in nodes.items() if p.get("phandle")==struct.pack(">I",value)]
        if len(found)!=1:raise ValueError("ambiguous pinctrl phandle")
        return found[0]
    ranges=cells(nodes[mapping["controller_path"]]["gpio-ranges"])
    if not ranges or len(ranges)%4:raise ValueError("invalid GPIO-to-pinctrl ranges")
    matches=[]
    for at in range(0,len(ranges),4):
        handle,first,pin,count=ranges[at:at+4]
        if first<=mapping["bit"]<first+count:matches.append((handle,pin+mapping["bit"]-first))
    if len(matches)!=1:raise ValueError("ambiguous Reset global pin")
    handle,pin=matches[0];path,controller=phandle(handle)
    if path!="/soc/pinctrl@10e20000" or controller.get("compatible")!=b"zte,zx-pinctrl\0" or cells(controller["reg"])!=(0,0x10e20000,0,0x1000):
        raise ValueError("unverified Reset pinctrl controller")
    first=0;field=None
    for bank in range(16):
        name="pinctrl-zx,bank-range-"+str(bank)
        if name not in controller:break
        values=cells(controller[name]);widths=[]
        if not values or len(values)%3:raise ValueError("invalid variable-width bank")
        for at in range(0,len(values),3):
            h,count,width=values[at:at+3];_,definition=phandle(h)
            if definition.get("#pinctrl-zx,bank-range-cells")!=struct.pack(">I",2) or not 1<=count<=32 or not 1<=width<=8:
                raise ValueError("unknown bank field definition")
            widths.extend([width]*count)
        if sum(widths)>32:raise ValueError("mux fields exceed register")
        if first<=pin<first+len(widths):
            local=pin-first;field=(bank*4,sum(widths[:local]),widths[local])
        first+=len(widths)
    gpio_ranges=cells(controller["pinctrl-zx,gpio-range"]);functions=[]
    if len(gpio_ranges)%4:raise ValueError("invalid pinctrl GPIO functions")
    for at in range(0,len(gpio_ranges),4):
        h,start,count,function=gpio_ranges[at:at+4];_,definition=phandle(h)
        if definition.get("#pinctrl-zx,gpio-range-cells")!=struct.pack(">I",3):raise ValueError("unknown GPIO function cells")
        if start<=pin<start+count:functions.append(function)
    if field is None or len(functions)!=1:raise ValueError("missing/ambiguous Reset mux field")
    offset,shift,width=field;function=functions[0]
    if (pin,offset,shift,width,function)!=(14,0,29,2,1):raise ValueError("Reset pinctrl differs from measured native request")
    return {"controller_path":path,"register":hex(0x10e20000+offset),"global_pin":pin,
            "shift":shift,"width":width,"mask":hex(((1<<width)-1)<<shift),
            "function":function,"value":hex(function<<shift)}
