# 第十二章 全等三角形

## 12.1 全等三角形的概念

两个三角形能够完全重合,称它们为全等三角形。重合的顶点叫做对应顶点,重合的边叫做对应边,重合的内角叫做对应角。

**定义 12.1** 若 $\triangle ABC \cong \triangle DEF$,则:

- 对应边相等:$AB = DE$, $BC = EF$, $CA = FD$;
- 对应角相等:$\angle A = \angle D$, $\angle B = \angle E$, $\angle C = \angle F$。

## 12.2 全等三角形的判定

### 12.2.1 边角边定理 (SAS)

**定理 12.1 (SAS)** 两边和它们的夹角分别相等的两个三角形全等。

若 $AB = DE$, $\angle A = \angle D$, $AC = DF$,则 $\triangle ABC \cong \triangle DEF$。

![三角形 ABC](https://mineru.example/tmp/img_p001_001.png)

### 12.2.2 角边角定理 (ASA)

**定理 12.2 (ASA)** 两角和它们的夹边分别相等的两个三角形全等。

### 12.2.3 边边边定理 (SSS)

**定理 12.3 (SSS)** 三边分别相等的两个三角形全等。

![两个全等三角形](https://mineru.example/tmp/img_p003_001.png)

## 12.3 例题

**例题 12.1** 如图,在 $\triangle ABC$ 中,$AB = AC$,点 $D$ 在 $AC$ 上,且 $BD = BC$。求证:$\angle ABD = \angle DBC$。

![例题 12.1 配图](https://mineru.example/tmp/img_p004_001.png)

**解** 由 $AB = AC$ 得 $\angle ABC = \angle ACB$。由 $BD = BC$ 得 $\angle BDC = \angle C$。从而 $\angle ABD = \angle ABC - \angle DBC = \angle ACB - \angle BDC = \angle DBC$。

**例题 12.2** 如图,在四边形 $PQRS$ 中,$PS \parallel QR$,且 $PS = QR$。求证:$PQRS$ 是平行四边形。

![例题 12.2 配图](https://mineru.example/tmp/img_p002_001.png)

**解** 连接 $PR$。由 $PS \parallel QR$ 且 $PS = QR$ 得 $\triangle PSR \cong \triangle RQP$ (SAS)。故 $\angle SPR = \angle QRP$,从而 $PR \parallel QS$。又 $PS = QR$,所以 $PQRS$ 是平行四边形。

## 12.4 习题

**习题 12.1** 已知 $\triangle ABC \cong \triangle DEF$,且 $AB = 5$, $BC = 7$, $CA = 8$。求 $\triangle DEF$ 的三边长。

**习题 12.2** 在 $\triangle ABC$ 与 $\triangle DBC$ 中,若 $AB = DB$, $\angle ABC = \angle DBC$, $BC = BC$。能否判定 $\triangle ABC \cong \triangle DBC$?为什么?

**习题 12.3** 如图,$AD \perp BC$ 于 $D$,$AE \perp BF$ 于 $E$。试说明 $\triangle ABD \cong \triangle EBD$ 的条件。

## 12.5 小结

本章学习了全等三角形的概念、四种基本判定方法(SAS、ASA、SSS、HL)以及典型应用。HL 判定将在下一章直角三角形中专门讨论。

鸡兔同笼:笼中有头 35 个,足 94 只,问鸡兔各几何?

> 注:此题为杂题,与全等三角形无关,用于演示大纲未匹配兜底。