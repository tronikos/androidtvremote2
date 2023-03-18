# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: polo.proto
"""Generated protocol buffer code."""
from google.protobuf.internal import builder as _builder
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\npolo.proto\x12\x12polo.wire.protobuf\"\xd1\x04\n\x0cOuterMessage\x12\x1b\n\x10protocol_version\x18\x01 \x02(\r:\x01\x31\x12\x37\n\x06status\x18\x02 \x02(\x0e\x32\'.polo.wire.protobuf.OuterMessage.Status\x12;\n\x0fpairing_request\x18\n \x01(\x0b\x32\".polo.wire.protobuf.PairingRequest\x12\x42\n\x13pairing_request_ack\x18\x0b \x01(\x0b\x32%.polo.wire.protobuf.PairingRequestAck\x12,\n\x07options\x18\x14 \x01(\x0b\x32\x1b.polo.wire.protobuf.Options\x12\x38\n\rconfiguration\x18\x1e \x01(\x0b\x32!.polo.wire.protobuf.Configuration\x12?\n\x11\x63onfiguration_ack\x18\x1f \x01(\x0b\x32$.polo.wire.protobuf.ConfigurationAck\x12*\n\x06secret\x18( \x01(\x0b\x32\x1a.polo.wire.protobuf.Secret\x12\x31\n\nsecret_ack\x18) \x01(\x0b\x32\x1d.polo.wire.protobuf.SecretAck\"b\n\x06Status\x12\x0e\n\tSTATUS_OK\x10\xc8\x01\x12\x11\n\x0cSTATUS_ERROR\x10\x90\x03\x12\x1d\n\x18STATUS_BAD_CONFIGURATION\x10\x91\x03\x12\x16\n\x11STATUS_BAD_SECRET\x10\x92\x03\";\n\x0ePairingRequest\x12\x14\n\x0cservice_name\x18\x01 \x02(\t\x12\x13\n\x0b\x63lient_name\x18\x02 \x01(\t\"(\n\x11PairingRequestAck\x12\x13\n\x0bserver_name\x18\x01 \x01(\t\"\x99\x04\n\x07Options\x12=\n\x0finput_encodings\x18\x01 \x03(\x0b\x32$.polo.wire.protobuf.Options.Encoding\x12>\n\x10output_encodings\x18\x02 \x03(\x0b\x32$.polo.wire.protobuf.Options.Encoding\x12<\n\x0epreferred_role\x18\x03 \x01(\x0e\x32$.polo.wire.protobuf.Options.RoleType\x1a\x82\x02\n\x08\x45ncoding\x12?\n\x04type\x18\x01 \x02(\x0e\x32\x31.polo.wire.protobuf.Options.Encoding.EncodingType\x12\x15\n\rsymbol_length\x18\x02 \x02(\r\"\x9d\x01\n\x0c\x45ncodingType\x12\x19\n\x15\x45NCODING_TYPE_UNKNOWN\x10\x00\x12\x1e\n\x1a\x45NCODING_TYPE_ALPHANUMERIC\x10\x01\x12\x19\n\x15\x45NCODING_TYPE_NUMERIC\x10\x02\x12\x1d\n\x19\x45NCODING_TYPE_HEXADECIMAL\x10\x03\x12\x18\n\x14\x45NCODING_TYPE_QRCODE\x10\x04\"L\n\x08RoleType\x12\x15\n\x11ROLE_TYPE_UNKNOWN\x10\x00\x12\x13\n\x0fROLE_TYPE_INPUT\x10\x01\x12\x14\n\x10ROLE_TYPE_OUTPUT\x10\x02\"\x82\x01\n\rConfiguration\x12\x36\n\x08\x65ncoding\x18\x01 \x02(\x0b\x32$.polo.wire.protobuf.Options.Encoding\x12\x39\n\x0b\x63lient_role\x18\x02 \x02(\x0e\x32$.polo.wire.protobuf.Options.RoleType\"\x12\n\x10\x43onfigurationAck\"\x18\n\x06Secret\x12\x0e\n\x06secret\x18\x01 \x02(\x0c\"\x1b\n\tSecretAck\x12\x0e\n\x06secret\x18\x01 \x02(\x0c\x42,\n\x1d\x63om.google.polo.wire.protobufB\tPoloProtoH\x03')

_builder.BuildMessageAndEnumDescriptors(DESCRIPTOR, globals())
_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'polo_pb2', globals())
if _descriptor._USE_C_DESCRIPTORS == False:

  DESCRIPTOR._options = None
  DESCRIPTOR._serialized_options = b'\n\035com.google.polo.wire.protobufB\tPoloProtoH\003'
  _OUTERMESSAGE._serialized_start=35
  _OUTERMESSAGE._serialized_end=628
  _OUTERMESSAGE_STATUS._serialized_start=530
  _OUTERMESSAGE_STATUS._serialized_end=628
  _PAIRINGREQUEST._serialized_start=630
  _PAIRINGREQUEST._serialized_end=689
  _PAIRINGREQUESTACK._serialized_start=691
  _PAIRINGREQUESTACK._serialized_end=731
  _OPTIONS._serialized_start=734
  _OPTIONS._serialized_end=1271
  _OPTIONS_ENCODING._serialized_start=935
  _OPTIONS_ENCODING._serialized_end=1193
  _OPTIONS_ENCODING_ENCODINGTYPE._serialized_start=1036
  _OPTIONS_ENCODING_ENCODINGTYPE._serialized_end=1193
  _OPTIONS_ROLETYPE._serialized_start=1195
  _OPTIONS_ROLETYPE._serialized_end=1271
  _CONFIGURATION._serialized_start=1274
  _CONFIGURATION._serialized_end=1404
  _CONFIGURATIONACK._serialized_start=1406
  _CONFIGURATIONACK._serialized_end=1424
  _SECRET._serialized_start=1426
  _SECRET._serialized_end=1450
  _SECRETACK._serialized_start=1452
  _SECRETACK._serialized_end=1479
# @@protoc_insertion_point(module_scope)
