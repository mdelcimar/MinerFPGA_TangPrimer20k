package tangminer

import spinal.core._
import spinal.lib._

object Sha256 {
  val Iv = List(
    BigInt("6a09e667", 16), BigInt("bb67ae85", 16),
    BigInt("3c6ef372", 16), BigInt("a54ff53a", 16),
    BigInt("510e527f", 16), BigInt("9b05688c", 16),
    BigInt("1f83d9ab", 16), BigInt("5be0cd19", 16)
  )

  val K = List(
    "428a2f98", "71374491", "b5c0fbcf", "e9b5dba5",
    "3956c25b", "59f111f1", "923f82a4", "ab1c5ed5",
    "d807aa98", "12835b01", "243185be", "550c7dc3",
    "72be5d74", "80deb1fe", "9bdc06a7", "c19bf174",
    "e49b69c1", "efbe4786", "0fc19dc6", "240ca1cc",
    "2de92c6f", "4a7484aa", "5cb0a9dc", "76f988da",
    "983e5152", "a831c66d", "b00327c8", "bf597fc7",
    "c6e00bf3", "d5a79147", "06ca6351", "14292967",
    "27b70a85", "2e1b2138", "4d2c6dfc", "53380d13",
    "650a7354", "766a0abb", "81c2c92e", "92722c85",
    "a2bfe8a1", "a81a664b", "c24b8b70", "c76c51a3",
    "d192e819", "d6990624", "f40e3585", "106aa070",
    "19a4c116", "1e376c08", "2748774c", "34b0bcb5",
    "391c0cb3", "4ed8aa4a", "5b9cca4f", "682e6ff3",
    "748f82ee", "78a5636f", "84c87814", "8cc70208",
    "90befffa", "a4506ceb", "bef9a3f7", "c67178f2"
  ).map(BigInt(_, 16))

  def word(value: BigInt): UInt = U(value, 32 bits)
  def wordFromBits(value: Bits, index: Int): UInt =
    value(511 - index * 32 downto 480 - index * 32).asUInt

  def rotr(x: UInt, n: Int): UInt = (x.rotateRight(n)).resize(32)
  def ch(x: UInt, y: UInt, z: UInt): UInt = ((x & y) ^ (~x & z)).resize(32)
  def maj(x: UInt, y: UInt, z: UInt): UInt = ((x & y) ^ (x & z) ^ (y & z)).resize(32)
  def bigSigma0(x: UInt): UInt = (rotr(x, 2) ^ rotr(x, 13) ^ rotr(x, 22)).resize(32)
  def bigSigma1(x: UInt): UInt = (rotr(x, 6) ^ rotr(x, 11) ^ rotr(x, 25)).resize(32)
  def smallSigma0(x: UInt): UInt = (rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3).resize(32)).resize(32)
  def smallSigma1(x: UInt): UInt = (rotr(x, 17) ^ rotr(x, 19) ^ (x >> 10).resize(32)).resize(32)

  def concatWords(words: Seq[UInt]): Bits = words.map(_.asBits).reduce(_ ## _)

  def reverseBytes256(value: Bits): Bits =
    (0 until 32).map(i => value(i * 8 + 7 downto i * 8)).reduce(_ ## _)

  def byteFromMsb(value: Bits, byteCount: Int, index: UInt): Bits = {
    val bytes = Vec((0 until byteCount).map(i => value(byteCount * 8 - 1 - i * 8 downto byteCount * 8 - 8 - i * 8)))
    bytes(index.resized)
  }
}

class UartRx(clksPerBit: Int) extends Component {
  val io = new Bundle {
    val rx = in Bool()
    val data = out Bits(8 bits)
    val valid = out Bool()
    val reset = in Bool()
  }

  object State extends SpinalEnum {
    val idle, start, data, stop = newElement()
  }

  val state = Reg(State()) init State.idle
  val clkCount = Reg(UInt(16 bits)) init 0
  val bitIndex = Reg(UInt(3 bits)) init 0
  val rxShift = Reg(Bits(8 bits)) init 0
  val rxMeta = Reg(Bool()) init True
  val rxSync = Reg(Bool()) init True
  val dataReg = Reg(Bits(8 bits)) init 0
  val validReg = Reg(Bool()) init False

  io.data := dataReg
  io.valid := validReg

  rxMeta := io.rx
  rxSync := rxMeta
  validReg := False

  when(io.reset) {
    state := State.idle
    clkCount := 0
    bitIndex := 0
    rxShift := 0
    rxMeta := True
    rxSync := True
    dataReg := 0
    validReg := False
  } otherwise {
    switch(state) {
      is(State.idle) {
        clkCount := 0
        bitIndex := 0
        when(!rxSync) {
          state := State.start
        }
      }
      is(State.start) {
        when(clkCount === U(clksPerBit / 2, 16 bits)) {
          clkCount := 0
          state := Mux(rxSync, State.idle, State.data)
        } otherwise {
          clkCount := clkCount + 1
        }
      }
      is(State.data) {
        when(clkCount === U(clksPerBit - 1, 16 bits)) {
          clkCount := 0
          rxShift(bitIndex) := rxSync
          when(bitIndex === 7) {
            bitIndex := 0
            state := State.stop
          } otherwise {
            bitIndex := bitIndex + 1
          }
        } otherwise {
          clkCount := clkCount + 1
        }
      }
      is(State.stop) {
        when(clkCount === U(clksPerBit - 1, 16 bits)) {
          dataReg := rxShift
          validReg := rxSync
          clkCount := 0
          state := State.idle
        } otherwise {
          clkCount := clkCount + 1
        }
      }
    }
  }
}

class UartTx(clksPerBit: Int) extends Component {
  val io = new Bundle {
    val start = in Bool()
    val data = in Bits(8 bits)
    val tx = out Bool()
    val busy = out Bool()
    val reset = in Bool()
  }

  object State extends SpinalEnum {
    val idle, start, data, stop = newElement()
  }

  val state = Reg(State()) init State.idle
  val clkCount = Reg(UInt(16 bits)) init 0
  val bitIndex = Reg(UInt(3 bits)) init 0
  val txShift = Reg(Bits(8 bits)) init 0
  val txReg = Reg(Bool()) init True
  val busyReg = Reg(Bool()) init False

  io.tx := txReg
  io.busy := busyReg

  when(io.reset) {
    state := State.idle
    clkCount := 0
    bitIndex := 0
    txShift := 0
    txReg := True
    busyReg := False
  } otherwise {
    switch(state) {
      is(State.idle) {
        txReg := True
        busyReg := False
        clkCount := 0
        bitIndex := 0
        when(io.start) {
          txShift := io.data
          busyReg := True
          state := State.start
        }
      }
      is(State.start) {
        txReg := False
        when(clkCount === U(clksPerBit - 1, 16 bits)) {
          clkCount := 0
          state := State.data
        } otherwise {
          clkCount := clkCount + 1
        }
      }
      is(State.data) {
        txReg := txShift(bitIndex)
        when(clkCount === U(clksPerBit - 1, 16 bits)) {
          clkCount := 0
          when(bitIndex === 7) {
            bitIndex := 0
            state := State.stop
          } otherwise {
            bitIndex := bitIndex + 1
          }
        } otherwise {
          clkCount := clkCount + 1
        }
      }
      is(State.stop) {
        txReg := True
        when(clkCount === U(clksPerBit - 1, 16 bits)) {
          clkCount := 0
          state := State.idle
        } otherwise {
          clkCount := clkCount + 1
        }
      }
    }
  }
}

class Sha256Compress extends Component {
  val io = new Bundle {
    val reset = in Bool()
    val start = in Bool()
    val stateIn = in Bits(256 bits)
    val block = in Bits(512 bits)
    val busy = out Bool()
    val done = out Bool()
    val stateOut = out Bits(256 bits)
  }

  val a, b, c, d, e, f, g, h = Reg(UInt(32 bits)) init 0
  val h0, h1, h2, h3, h4, h5, h6, h7 = Reg(UInt(32 bits)) init 0
  val w = Vec(Reg(UInt(32 bits)) init 0, 16)
  val round = Reg(UInt(7 bits)) init 0
  val busyReg = Reg(Bool()) init False
  val doneReg = Reg(Bool()) init False
  val stateOutReg = Reg(Bits(256 bits)) init 0

  val kVec = Vec(Sha256.K.map(Sha256.word))
  val wNext = (Sha256.smallSigma1(w(14)) + w(9) + Sha256.smallSigma0(w(1)) + w(0)).resize(32)
  val t1 = (h + Sha256.bigSigma1(e) + Sha256.ch(e, f, g) + kVec(round(5 downto 0)) + w(0)).resize(32)
  val t2 = (Sha256.bigSigma0(a) + Sha256.maj(a, b, c)).resize(32)

  io.busy := busyReg
  io.done := doneReg
  io.stateOut := stateOutReg

  when(io.reset) {
    a := 0; b := 0; c := 0; d := 0; e := 0; f := 0; g := 0; h := 0
    h0 := 0; h1 := 0; h2 := 0; h3 := 0; h4 := 0; h5 := 0; h6 := 0; h7 := 0
    for (i <- 0 until 16) w(i) := 0
    round := 0
    busyReg := False
    doneReg := False
    stateOutReg := 0
  } otherwise {
    doneReg := False

    when(io.start && !busyReg) {
      h0 := io.stateIn(255 downto 224).asUInt
      h1 := io.stateIn(223 downto 192).asUInt
      h2 := io.stateIn(191 downto 160).asUInt
      h3 := io.stateIn(159 downto 128).asUInt
      h4 := io.stateIn(127 downto 96).asUInt
      h5 := io.stateIn(95 downto 64).asUInt
      h6 := io.stateIn(63 downto 32).asUInt
      h7 := io.stateIn(31 downto 0).asUInt

      a := io.stateIn(255 downto 224).asUInt
      b := io.stateIn(223 downto 192).asUInt
      c := io.stateIn(191 downto 160).asUInt
      d := io.stateIn(159 downto 128).asUInt
      e := io.stateIn(127 downto 96).asUInt
      f := io.stateIn(95 downto 64).asUInt
      g := io.stateIn(63 downto 32).asUInt
      h := io.stateIn(31 downto 0).asUInt

      for (i <- 0 until 16) {
        w(i) := Sha256.wordFromBits(io.block, i)
      }

      round := 0
      busyReg := True
    } elsewhen(busyReg) {
      for (i <- 0 until 15) {
        w(i) := w(i + 1)
      }
      w(15) := wNext

      h := g
      g := f
      f := e
      e := (d + t1).resize(32)
      d := c
      c := b
      b := a
      a := (t1 + t2).resize(32)

      when(round === 63) {
        stateOutReg := Sha256.concatWords(Seq(
          (h0 + t1 + t2).resize(32),
          (h1 + a).resize(32),
          (h2 + b).resize(32),
          (h3 + c).resize(32),
          (h4 + d + t1).resize(32),
          (h5 + e).resize(32),
          (h6 + f).resize(32),
          (h7 + g).resize(32)
        ))
        busyReg := False
        doneReg := True
      } otherwise {
        round := round + 1
      }
    }
  }
}

class BitcoinHashCore extends Component {
  val io = new Bundle {
    val reset = in Bool()
    val start = in Bool()
    val stop = in Bool()
    val midstate = in Bits(256 bits)
    val tail = in Bits(96 bits)
    val target = in Bits(256 bits)
    val running = out Bool()
    val found = out Bool()
    val foundNonce = out UInt(32 bits)
    val foundHash = out Bits(256 bits)
    val currentNonce = out UInt(32 bits)
  }

  object State extends SpinalEnum {
    val idle, firstStart, firstWait, secondStart, secondWait, report = newElement()
  }

  val state = Reg(State()) init State.idle
  val shaStart = Reg(Bool()) init False
  val shaStateIn = Reg(Bits(256 bits)) init 0
  val shaBlock = Reg(Bits(512 bits)) init 0
  val firstDigest = Reg(Bits(256 bits)) init 0
  val runningReg = Reg(Bool()) init False
  val foundReg = Reg(Bool()) init False
  val foundNonceReg = Reg(UInt(32 bits)) init 0
  val foundHashReg = Reg(Bits(256 bits)) init 0
  val currentNonceReg = Reg(UInt(32 bits)) init 0

  val sha = new Sha256Compress
  sha.io.reset := io.reset
  sha.io.start := shaStart
  sha.io.stateIn := shaStateIn
  sha.io.block := shaBlock

  val shaIv = B(Sha256.Iv.map(v => B(v, 32 bits)).reduce(_ ## _))
  val firstBlock =
    io.tail(95 downto 64) ## io.tail(63 downto 32) ## io.tail(31 downto 0) ## currentNonceReg.asBits ##
      B"32'h80000000" ## B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ##
      B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ##
      B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ## B"32'h00000280"
  val secondBlock =
    firstDigest ##
      B"32'h80000000" ## B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ##
      B"32'h00000000" ## B"32'h00000000" ## B"32'h00000000" ## B"32'h00000100"

  io.running := runningReg
  io.found := foundReg
  io.foundNonce := foundNonceReg
  io.foundHash := foundHashReg
  io.currentNonce := currentNonceReg

  when(io.reset) {
    state := State.idle
    shaStart := False
    shaStateIn := 0
    shaBlock := 0
    firstDigest := 0
    runningReg := False
    foundReg := False
    foundNonceReg := 0
    foundHashReg := 0
    currentNonceReg := 0
  } otherwise {
    shaStart := False

    when(io.stop) {
      state := State.idle
      runningReg := False
    } otherwise {
      switch(state) {
        is(State.idle) {
          foundReg := False
          runningReg := False
          when(io.start) {
            currentNonceReg := 0
            runningReg := True
            state := State.firstStart
          }
        }
        is(State.firstStart) {
          when(!sha.io.busy) {
            shaStateIn := io.midstate
            shaBlock := firstBlock
            shaStart := True
            state := State.firstWait
          }
        }
        is(State.firstWait) {
          when(sha.io.done) {
            firstDigest := sha.io.stateOut
            state := State.secondStart
          }
        }
        is(State.secondStart) {
          when(!sha.io.busy) {
            shaStateIn := shaIv
            shaBlock := secondBlock
            shaStart := True
            state := State.secondWait
          }
        }
        is(State.secondWait) {
          when(sha.io.done) {
            when(Sha256.reverseBytes256(sha.io.stateOut).asUInt <= io.target.asUInt) {
              foundReg := True
              foundNonceReg := currentNonceReg
              foundHashReg := sha.io.stateOut
              state := State.report
            } otherwise {
              currentNonceReg := currentNonceReg + 1
              state := State.firstStart
            }
          }
        }
        is(State.report) {
          runningReg := False
          when(io.start) {
            foundReg := False
            currentNonceReg := 0
            runningReg := True
            state := State.firstStart
          }
        }
      }
    }
  }
}

class Top extends Component {
  setDefinitionName("top")
  noIoPrefix()

  val io = new Bundle {
    val clk = in Bool()
    val uart_rx_pin = in Bool()
    val uart_tx_pin = out Bool()
    val led = out Bits(6 bits)
  }

  val coreArea = new ClockingArea(ClockDomain(io.clk, config = ClockDomainConfig(resetKind = BOOT))) {
    val ClksPerBit = 234
    val JobBytes = 76
    val FoundRespBytes = 37
    val EchoRespBytes = 77

    val resetCounter = Reg(UInt(24 bits)) init 0
    val reset = !resetCounter.msb
    when(!resetCounter.msb) {
      resetCounter := resetCounter + 1
    }

    val rx = new UartRx(ClksPerBit)
    rx.io.reset := reset
    rx.io.rx := io.uart_rx_pin

    val tx = new UartTx(ClksPerBit)
    tx.io.reset := reset

    val core = new BitcoinHashCore
    core.io.reset := reset

    object RxState extends SpinalEnum {
      val sync0, sync1, cmd, payload = newElement()
    }

    object TxState extends SpinalEnum {
      val idle, send, waitBusy = newElement()
    }

    val rxState = Reg(RxState()) init RxState.sync0
    val payloadCount = Reg(UInt(7 bits)) init 0
    val command = Reg(Bits(8 bits)) init 0
    val midstate = Reg(Bits(256 bits)) init 0
    val tail = Reg(Bits(96 bits)) init 0
    val target = Reg(Bits(256 bits)) init 0
    val coreStart = Reg(Bool()) init False
    val coreStop = Reg(Bool()) init False
    val coreStartPending = Reg(Bool()) init False
    val echoToggle = Reg(Bool()) init False

    core.io.start := coreStart
    core.io.stop := coreStop
    core.io.midstate := midstate
    core.io.tail := tail
    core.io.target := target

    when(reset) {
      rxState := RxState.sync0
      payloadCount := 0
      command := 0
      coreStart := False
      coreStop := False
      coreStartPending := False
      midstate := 0
      tail := 0
      target := 0
      echoToggle := False
    } otherwise {
      coreStart := False
      coreStop := False

      when(coreStartPending) {
        coreStart := True
        coreStartPending := False
      }

      when(rx.io.valid) {
        switch(rxState) {
          is(RxState.sync0) {
            rxState := Mux(rx.io.data === B"8'h54", RxState.sync1, RxState.sync0)
          }
          is(RxState.sync1) {
            rxState := Mux(rx.io.data === B"8'h4e", RxState.cmd, RxState.sync0)
          }
          is(RxState.cmd) {
            command := rx.io.data
            payloadCount := 0
            when(rx.io.data === B"8'h53") {
              coreStop := True
              rxState := RxState.sync0
            } elsewhen(rx.io.data === B"8'h48") {
              midstate := B"256'hbc909a336358bff090ccac7d1e59caa8c3c8d8e94f0103c896b187364719f91b"
              tail := B"96'h4b1e5e4a29ab5f49ffff001d"
              target := B"256'hffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
              coreStartPending := True
              rxState := RxState.sync0
            } elsewhen(rx.io.data === B"8'h4a" || rx.io.data === B"8'h45") {
              rxState := RxState.payload
            } otherwise {
              rxState := RxState.sync0
            }
          }
          is(RxState.payload) {
            when(payloadCount < 32) {
              midstate := midstate(247 downto 0) ## rx.io.data
            } elsewhen(payloadCount < 44) {
              tail := tail(87 downto 0) ## rx.io.data
            } elsewhen(payloadCount < 76) {
              target := target(247 downto 0) ## rx.io.data
            }

            when(payloadCount === JobBytes - 1) {
              when(command === B"8'h4a") {
                coreStartPending := True
              } elsewhen(command === B"8'h45") {
                echoToggle := !echoToggle
              }
              rxState := RxState.sync0
            } otherwise {
              payloadCount := payloadCount + 1
            }
          }
        }
      }
    }

    def foundResponseByte(index: UInt): Bits = {
      val nonceBytes = (0 until 4).map(i => core.io.foundNonce.asBits(31 - i * 8 downto 24 - i * 8))
      val hashBytes = (0 until 32).map(i => core.io.foundHash(255 - i * 8 downto 248 - i * 8))
      val bytes = Vec(Seq(B"8'h46") ++ nonceBytes ++ hashBytes)
      bytes(index.resized)
    }

    def echoResponseByte(index: UInt): Bits = {
      val midstateBytes = (0 until 32).map(i => midstate(255 - i * 8 downto 248 - i * 8))
      val tailBytes = (0 until 12).map(i => tail(95 - i * 8 downto 88 - i * 8))
      val targetBytes = (0 until 32).map(i => target(255 - i * 8 downto 248 - i * 8))
      val bytes = Vec(Seq(B"8'h45") ++ midstateBytes ++ tailBytes ++ targetBytes)
      bytes(index.resized)
    }

    val txState = Reg(TxState()) init TxState.idle
    val txIndex = Reg(UInt(7 bits)) init 0
    val txStart = Reg(Bool()) init False
    val txData = Reg(Bits(8 bits)) init B"8'hff"
    val foundSeen = Reg(Bool()) init False
    val echoSeenToggle = Reg(Bool()) init False
    val txEcho = Reg(Bool()) init False

    tx.io.start := txStart
    tx.io.data := txData
    io.uart_tx_pin := tx.io.tx

    when(reset) {
      txState := TxState.idle
      txIndex := 0
      txStart := False
      txData := B"8'hff"
      foundSeen := False
      echoSeenToggle := False
      txEcho := False
    } otherwise {
      txStart := False

      when(!core.io.found) {
        foundSeen := False
      }

      switch(txState) {
        is(TxState.idle) {
          when(echoSeenToggle =/= echoToggle) {
            txIndex := 0
            txEcho := True
            txState := TxState.send
            echoSeenToggle := echoToggle
          } elsewhen(core.io.found && !foundSeen) {
            txIndex := 0
            txEcho := False
            txState := TxState.send
            foundSeen := True
          }
        }
        is(TxState.send) {
          when(!tx.io.busy) {
            txData := Mux(txEcho, echoResponseByte(txIndex), foundResponseByte(txIndex))
            txStart := True
            txState := TxState.waitBusy
          }
        }
        is(TxState.waitBusy) {
          when(tx.io.busy) {
            when((!txEcho && txIndex === FoundRespBytes - 1) || (txEcho && txIndex === EchoRespBytes - 1)) {
              txState := TxState.idle
            } otherwise {
              txIndex := txIndex + 1
              txState := TxState.send
            }
          }
        }
      }
    }

    io.led(0) := !core.io.running
    io.led(1) := !core.io.found
    io.led(2) := !core.io.currentNonce(20)
    io.led(3) := !core.io.currentNonce(21)
    io.led(4) := !core.io.currentNonce(22)
    io.led(5) := !core.io.currentNonce(23)
  }
}

object GenerateVerilog extends App {
  SpinalConfig(
    targetDirectory = "build/spinal",
    defaultConfigForClockDomains = ClockDomainConfig(resetKind = BOOT)
  ).generateVerilog(new Top)
}
